"""Offline EXIF and previews for Windows; no scene model or network requests."""
import base64
import io
import json
from pathlib import Path
import subprocess
import sys
import threading

_raw_slots = threading.BoundedSemaphore(2)
_plugin_lock = threading.Lock()
_plugins_ready = False
HIGH_EDGE = 3200


def _register_plugins():
    global _plugins_ready
    with _plugin_lock:
        if not _plugins_ready:
            try:
                from pi_heif import register_heif_opener
                register_heif_opener()
            except ImportError:
                pass
            _plugins_ready = True


def _orientation(value):
    """EXIF has eight transforms; keep missing distinct from explicit normal (1)."""
    try:
        values = getattr(value, 'values', value)
        number = int(values[0] if isinstance(values, (list, tuple)) else values)
        return number if 1 <= number <= 8 else None
    except (TypeError, ValueError, IndexError):
        return None


def _tag_orientation(tags, thumbnail=False):
    if thumbnail:
        value = _orientation(tags.get('Thumbnail Orientation'))
        if value is not None:
            return value
    return _orientation(tags.get('Image Orientation'))


def _encode(image, quality, source, fallback_orientation=None):
    from PIL import ImageOps
    # TIFF/HEIF loaders may normalize orientation during load; read the remaining
    # EXIF only afterwards, so an already-normalized image is not rotated twice.
    image.load()
    # The preview's own explicit orientation (including 1) wins. Otherwise inherit
    # the thumbnail IFD/RAW orientation. Bake it into pixels and emit no EXIF tag.
    orientation = _orientation(image.getexif().get(274))
    if orientation is None:
        orientation = _orientation(fallback_orientation) or 1
    with image.copy() as copy:
        copy.getexif()[274] = orientation
        with ImageOps.exif_transpose(copy) as oriented:
            oriented.thumbnail((HIGH_EDGE, HIGH_EDGE) if quality == 'high' else (480, 480))
            output = io.BytesIO()
            oriented.convert('RGB').save(output, format='JPEG', quality=90 if quality == 'high' else 75)
            return {'thumbnail': base64.b64encode(output.getvalue()).decode('ascii'),
                    'width': oriented.width, 'height': oriented.height, 'source': source}


def _preview_size(result):
    return result.get('width', 0) * result.get('height', 0) if result.get('thumbnail') else 0


def _sufficient(result):
    return bool(result.get('thumbnail')) and max(result.get('width', 0), result.get('height', 0)) >= HIGH_EDGE


def _decode_raw(path, quality):
    """Runs only in an isolated helper: native decoder failures cannot close the UI."""
    import rawpy
    from PIL import Image
    try:
        tags = tags_for(path, True)
    except Exception:
        tags = {}
    with rawpy.imread(str(path)) as raw:
        # LibRaw normally rotates automatically. Disable that for postprocess and
        # normalize here, through the same eight-way path as embedded previews.
        orientation = _tag_orientation(tags)
        if orientation is None:
            orientation = {0: 1, 3: 3, 5: 8, 6: 6}.get(raw.sizes.flip, 1)
        candidate = {'thumbnail': None}
        try:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                # Thumbnail IFD tags describe EXIF's small JPEG, not necessarily
                # a different, larger preview selected by LibRaw.
                thumb_orientation = _tag_orientation(tags, True) if thumb.data == tags.get('JPEGThumbnail') else orientation
                if thumb_orientation is None:
                    thumb_orientation = orientation
                with Image.open(io.BytesIO(thumb.data)) as image:
                    candidate = _encode(image, quality, 'RAW 内嵌预览', thumb_orientation)
            if thumb.format == rawpy.ThumbFormat.BITMAP:
                with Image.fromarray(thumb.data) as image:
                    candidate = _encode(image, quality, 'RAW 内嵌预览', orientation)
        except Exception:
            # A corrupt/unsupported embedded image must not prevent RAW decoding.
            pass
        if candidate.get('thumbnail') and (quality == 'low' or _sufficient(candidate)):
            return candidate
        try:
            # High quality needs native-size pixels, not an enlarged thumbnail.
            rgb = raw.postprocess(half_size=quality != 'high', user_flip=0,
                                  use_camera_wb=True, output_bps=8,
                                  demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR)
            with Image.fromarray(rgb) as image:
                decoded = _encode(image, quality, 'RAW 解码预览', orientation)
            if _preview_size(decoded) >= _preview_size(candidate):
                return decoded
            candidate['warning'] = '已尝试 RAW 解码，当前内嵌预览尺寸更大（未放大）'
        except Exception:
            candidate['warning'] = 'RAW 解码未成功，显示现有内嵌预览（未放大）' if candidate.get('thumbnail') else '此 RAW 暂时无法解码预览，仍可扫描和复制原文件'
        return candidate


def _raw_preview(path, quality):
    # Failures, native crashes and timeouts degrade to a format placeholder.
    if not _raw_slots.acquire(timeout=1):
        return {'thumbnail': None, 'warning': '预览正忙，请稍后重试；仍可扫描和复制'}
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--raw-preview',
                                 str(Path(path).resolve()), quality], capture_output=True,
                                timeout=25, creationflags=flags)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if isinstance(data, dict) and 'thumbnail' in data:
                return data
    except subprocess.TimeoutExpired:
        return {'thumbnail': None, 'warning': 'RAW 预览读取超时'}
    except (OSError, ValueError):
        pass
    finally:
        _raw_slots.release()
    return {'thumbnail': None, 'warning': '此 RAW 暂时无法预览，仍可扫描和复制原文件'}


def tags_for(path, thumbnail=False):
    import exifread
    with open(path, 'rb') as stream:
        return exifread.process_file(stream, details=False, extract_thumbnail=thumbnail)


def metadata(path, scene=False):
    try:
        tags = tags_for(path)
        date = str(tags.get('EXIF DateTimeOriginal', ''))
        result = {'date': date, 'reliable_time': bool(date),
                  'subsecond': str(tags.get('EXIF SubSecTimeOriginal', '')),
                  'camera': ' / '.join(str(tags[k]) for k in ('Image Make','Image Model','EXIF BodySerialNumber') if k in tags)}
        if not date: result['warning']='此格式未读取到拍摄时间，使用文件修改日期；延时分组需确认'
        return result
    except Exception:
        return {'warning':'此格式暂不支持元数据读取，使用修改日期，仍可复制原文件'}


def thumbnail(path, quality='low', raw=False):
    from PIL import Image
    if quality not in ('low','high'):raise ValueError('预览清晰度无效')
    try:
        if raw:
            candidate = {'thumbnail': None}
            try:
                tags = tags_for(path, True)
                embedded = tags.get('JPEGThumbnail')
                if embedded:
                    with Image.open(io.BytesIO(embedded)) as image:
                        candidate = _encode(image, quality, 'RAW 内嵌预览', _tag_orientation(tags, True))
            except Exception:
                pass
            if candidate.get('thumbnail') and (quality == 'low' or _sufficient(candidate)):
                return candidate
            # LibRaw may expose a larger embedded image than EXIF's JPEGThumbnail;
            # the helper tries it before decoding native RAW pixels.
            alternative = _raw_preview(path, quality)
            if alternative.get('thumbnail') and _preview_size(alternative) >= _preview_size(candidate):
                return alternative
            if candidate.get('thumbnail'):
                reason = alternative.get('warning', '')
                if alternative.get('thumbnail'):
                    reason = '已尝试 RAW 解码，内嵌预览尺寸更大'
                elif '超时' in reason:
                    reason = 'RAW 解码超时'
                elif '正忙' in reason:
                    reason = 'RAW 解码正忙'
                elif '不支持' in reason:
                    reason = '此 RAW 格式暂不支持解码'
                else:
                    reason = 'RAW 解码未成功'
                candidate['warning'] = reason + '；保留现有预览，未放大'
                return candidate
            return alternative
        _register_plugins()
        with Image.open(path) as image:
            return _encode(image, quality, '照片预览')
    except Exception:
        return {'thumbnail':None,'warning':'此格式暂时无法预览，仍可复制原文件'}


if __name__ == '__main__' and len(sys.argv) == 4 and sys.argv[1] == '--raw-preview':
    try:
        if sys.argv[3] not in ('low', 'high'):
            raise ValueError('Invalid preview quality')
        print(json.dumps(_decode_raw(sys.argv[2], sys.argv[3])))
    except Exception as exc:
        reason = '此 RAW 格式暂不支持解码' if type(exc).__name__ == 'LibRawFileUnsupportedError' else 'RAW 解码未成功'
        print(json.dumps({'thumbnail': None, 'warning': reason}))
