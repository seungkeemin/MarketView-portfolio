# 저장소 폴더와 바탕화면에 실행 아이콘을 만든다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1
#
# .lnk 은 커밋되지 않고(.gitignore) 저장소에는 이 생성기만 남는다. 저장소를 옮겼으면 다시 돌린다.

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $root 'scripts\start.bat'
$icon = Join-Path $root 'scripts\marketview.ico'

if (-not (Test-Path $bat))  { throw "실행 스크립트가 없습니다: $bat" }
if (-not (Test-Path $icon)) { throw "아이콘이 없습니다: $icon  (.venv\Scripts\python scripts\make_icon.py 로 만듭니다)" }

$targets = @(
    (Join-Path $root 'MarketView.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'MarketView.lnk')
)

$shell = New-Object -ComObject WScript.Shell
foreach ($link in $targets) {
    $shortcut = $shell.CreateShortcut($link)
    $shortcut.TargetPath       = $bat
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation     = "$icon,0"
    $shortcut.Description      = 'MarketView - 서버를 띄우고 Chrome 앱 모드로 연다'
    $shortcut.WindowStyle      = 7   # 최소화: 런처 콘솔이 번쩍이지 않게
    $shortcut.Save()
    Write-Host "만들었습니다: $link"
}
