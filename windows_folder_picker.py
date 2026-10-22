"""Read-only thumbnail directory chooser, isolated from the photo workflow."""
from collections import OrderedDict
import ctypes
import os
from pathlib import Path
import secrets
import threading

import windows_media

RAW = frozenset('cr2 cr3 nef nrw arw raf dng orf rw2 pef srw 3fr fff iiq kdc dcr mef mos mrw rwl x3f srf sr2 erf'.split())
IMAGES = frozenset('jpg jpeg heic heif hif png tif tiff webp gif bmp avif jxl'.split())
PAGE_SIZE = 120


def _roots():
    if os.name != 'nt':
        return [{'name': '文件系统', 'path': '/'}]
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [{'name': f'{chr(65+i)}:', 'path': f'{chr(65+i)}:\\'}
            for i in range(26) if mask & (1 << i)]


class FolderPickerAPI:
    def __init__(self, initial=None, roots=None, multiple=False):
        self._initial = str(initial or Path.home())
        self._roots = roots if roots is not None else _roots()
        self._lock = threading.Lock()
        self._preview_slots = threading.BoundedSemaphore(2)
        self._done = threading.Event()
        self._path = None
        self._listing = ''
        self._request = 0
        self._files = {}
        self._cache = OrderedDict()
        self._window = None
        self._result = ''
        self._multiple = bool(multiple)
        self._selected = []
        self._directories = set()

    def browse(self, path=None, offset=0, request=0):
        try:
            request = int(request)
            with self._lock:
                if request < self._request:
                    return {'error': '目录已切换。'}
                self._request = request
            offset = max(0, int(offset))
            folder = Path(path or self._initial).expanduser().resolve(strict=True)
            if not folder.is_dir():
                return {'error': '这里不是文件夹，请输入文件夹路径。'}
            entries = []
            with os.scandir(folder) as scan:
                for entry in scan:
                    if entry.name.startswith('.') or entry.is_symlink():
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        if not is_dir and not entry.is_file(follow_symlinks=False):
                            continue
                        entries.append((not is_dir, entry.name.casefold(), entry.name))
                    except OSError:
                        continue
            entries.sort()
            listing = secrets.token_urlsafe(18)
            files, items = {}, []
            offset = min(offset, max(0, (len(entries)-1)//PAGE_SIZE*PAGE_SIZE))
            for not_dir, _, name in entries[offset:offset+PAGE_SIZE]:
                file = folder / name
                ext = file.suffix.lower().lstrip('.')
                key = secrets.token_urlsafe(12)
                photo = not_dir and ext in (RAW | IMAGES)
                if photo:
                    files[key] = file
                items.append({'id': key, 'name': name, 'folder': not not_dir,
                              'path': str(file) if not not_dir else '',
                              'format': ext.upper() or '文件', 'photo': bool(photo)})
            with self._lock:
                if self._done.is_set() or request != self._request:
                    return {'error': '窗口已关闭。'}
                self._path, self._listing, self._files = folder, listing, files
                self._directories = {str(folder)} | {item['path'] for item in items if item['folder']}
            return {'path': str(folder), 'parent': str(folder.parent), 'listing': listing,
                    'items': items, 'total': len(entries), 'offset': offset, 'page_size': PAGE_SIZE,
                    'roots': self._roots, 'home': self._initial, 'multiple':self._multiple, 'selected':list(self._selected)}
        except (OSError, ValueError, TypeError):
            return {'error': '无法打开此文件夹，请检查路径、磁盘连接和访问权限。'}

    def preview(self, listing, key):
        with self._lock:
            file = self._files.get(key) if listing == self._listing and not self._done.is_set() else None
        if file is None:
            return {'thumbnail': None, 'warning': '目录已切换。'}
        try:
            info = file.stat()
            cache_key = (str(file), info.st_size, info.st_mtime_ns)
            with self._lock:
                if cache_key in self._cache:
                    self._cache.move_to_end(cache_key)
                    return self._cache[cache_key]
            with self._preview_slots:
                if self._done.is_set():
                    return {'thumbnail': None}
                result = windows_media.thumbnail(file, 'low', file.suffix.lower().lstrip('.') in RAW)
            with self._lock:
                self._cache[cache_key] = result
                while len(self._cache) > 128:
                    self._cache.popitem(last=False)
            return result
        except OSError:
            return {'thumbnail': None, 'warning': '文件已移动或无法读取。'}

    def toggle(self, listing, path):
        with self._lock:
            if not self._multiple or self._done.is_set() or listing!=self._listing or path not in self._directories:
                return {'error':'目录已变化，请重新选择。'}
            folder=Path(path)
            if folder.resolve()!=folder or not folder.is_dir():return {'error':'文件夹已不可用。'}
            if path in self._selected:self._selected.remove(path)
            elif len(self._selected)>=100:return {'error':'一次最多选择 100 个来源文件夹。'}
            else:self._selected.append(path)
            return {'selected':list(self._selected)}

    def choose(self, listing):
        with self._lock:
            if self._done.is_set() or listing != self._listing or self._path is None:
                return {'error': '目录已变化，请重新确认。'}
            if not self._path.is_dir():
                return {'error': '此文件夹已不可用，请重新选择。'}
            if self._multiple:
                if not self._selected:return {'error':'请先勾选来源文件夹，或添加当前文件夹。'}
                if any(not Path(p).is_dir() or Path(p).resolve()!=Path(p) for p in self._selected):return {'error':'已选来源中有文件夹不可用，请重新选择。'}
                self._result = list(self._selected)
            else:self._result = str(self._path)
            self._done.set()
        if self._window:
            self._window.destroy()
        return {'ok': True}

    def cancel(self):
        self._done.set()
        if self._window:
            self._window.destroy()
        return {'ok': True}


def choose_folder(initial=None, cancel_event=None, multiple=False):
    import webview
    api = FolderPickerAPI(initial,multiple=multiple)
    page = Path(__file__).with_name('windows_folder_picker.html').read_text(encoding='utf-8')
    window = webview.create_window('选择文件夹 · 拾光', html=page, js_api=api,
                                   width=1000, height=740, min_size=(660, 480))
    api._window = window
    window.events.closed += api._done.set
    while not api._done.wait(.2):
        if cancel_event is not None and cancel_event.is_set():
            api.cancel()
    return api._result
