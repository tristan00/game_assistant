# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for game_assistant.
# Build:  pyinstaller game_assistant.spec
# Output: dist/game_assistant.exe

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # pywin32 sometimes can't be discovered automatically
        'win32timezone',
        # pynput platform backends
        'pynput.keyboard._win32',
        'pynput.mouse._win32',
        # keyring backends (Windows Credential Manager)
        'keyring.backends.Windows',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim a bit of fat from PySide6
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DExtras',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DRender',
        'PySide6.QtBluetooth',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtNetworkAuth',
        'PySide6.QtNfc',
        'PySide6.QtPositioning',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuick3D',
        'PySide6.QtQuickControls2',
        'PySide6.QtQuickWidgets',
        'PySide6.QtRemoteObjects',
        'PySide6.QtScxml',
        'PySide6.QtSensors',
        'PySide6.QtSerialBus',
        'PySide6.QtSerialPort',
        'PySide6.QtSpatialAudio',
        'PySide6.QtSql',
        'PySide6.QtStateMachine',
        'PySide6.QtTest',
        'PySide6.QtWebChannel',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebSockets',
    ],
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
