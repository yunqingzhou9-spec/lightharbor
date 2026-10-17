"""Offline EXIF and previews for Windows; no scene model or network requests."""
import base64
import io
from pathlib import Path


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
        def coord(key):
            return sum(float(v.num)/v.den/div for v,div in zip(tags[key].values,(1,60,3600)))
        try:
            lat,lon=coord('GPS GPSLatitude'),coord('GPS GPSLongitude')
            if str(tags.get('GPS GPSLatitudeRef'))=='S': lat=-lat
            if str(tags.get('GPS GPSLongitudeRef'))=='W': lon=-lon
            result['gps']=[lat,lon]
        except (KeyError,ValueError,ZeroDivisionError,AttributeError): pass
        if not date: result['warning']='此格式未读取到拍摄时间，使用文件修改日期；延时分组需确认'
        if scene: result['warning']='Windows 版暂不支持景物识别'
        return result
    except Exception:
        return {'warning':'此格式暂不支持元数据读取，使用修改日期，仍可复制原文件'}


def thumbnail(path):
    from PIL import Image, ImageOps
    try:
        try:
            im=Image.open(path)
        except Exception:
            embedded=tags_for(path,True).get('JPEGThumbnail')
            if not embedded:return {'thumbnail':None,'warning':'此 RAW 格式没有可用预览，仍可复制'}
            im=Image.open(io.BytesIO(embedded))
        with im:
            im.thumbnail((480,480))
            output=io.BytesIO()
            ImageOps.exif_transpose(im).convert('RGB').save(output,format='JPEG',quality=80)
            return {'thumbnail':base64.b64encode(output.getvalue()).decode('ascii')}
    except Exception:
        return {'thumbnail':None,'warning':'此格式暂时无法预览，仍可复制原文件'}
