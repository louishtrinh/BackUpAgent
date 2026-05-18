# backup_agent.spec
# Build with:  pyinstaller backup_agent.spec

a = Analysis(
    ['backup_agent.py'],
    hiddenimports=[
        # watchdog loads its Windows backend dynamically — must be explicit
        'watchdog.observers.winapi',
        'watchdog.observers.polling',
        'watchdog.events',
    ],
    datas=[],
    hookspath=[],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='backup_agent',
    console=False,   # no console window — runs silently in background
    onefile=True,
)
