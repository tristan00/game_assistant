# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for game_assistant.
# Build:  pyinstaller game_assistant.spec
# Output: dist/game_assistant.exe

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Web UI frontend assets. Bundled at the same relative path so
        # `Path(__file__).parent / "static"` resolves inside _MEIPASS.
        ('app/static', 'app/static'),
    ],
    hiddenimports=[
        # pywin32 sometimes can't be discovered automatically
        'win32timezone',
        # pynput platform backends
        'pynput.keyboard._win32',
        'pynput.mouse._win32',
        # keyring backends (Windows Credential Manager)
        'keyring.backends.Windows',
        # uvicorn worker / protocol modules (loaded dynamically by uvicorn)
        'uvicorn.loops.asyncio',
        'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.protocols.websockets.wsproto_impl',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        # pywebview Windows / WebView2 backend (loaded by pywebview at runtime)
        'webview.platforms.edgechromium',
        'webview.platforms.winforms',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='game_assistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI app — logs go to ~/game_assistant/logs/run.log
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
