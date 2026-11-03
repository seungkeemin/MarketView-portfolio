# MarketView 실행. 바탕화면 아이콘이 start.bat 을 거쳐 이 파일을 부른다.
#
#   1. 서버가 실제로 응답하는지 본다. 떠 있으면 다시 띄우지 않으므로 아이콘을 두 번 눌러도 안전하다.
#   2. 없으면 최소화된 창으로 띄우고 응답할 때까지 기다린다 (앱이 시작되면 백그라운드 수집·백업이 알아서 돈다).
#   3. Chrome 앱 모드로 연다 (탭·주소창 없음). Chrome 이 없으면 기본 브라우저.
#
# 서버를 끄려면 작업표시줄의 최소화된 python 창에서 Ctrl+C.
# 주소는 localhost 가 아니라 127.0.0.1 이다 (Windows 에서 ::1 우선 해석으로 요청당 2초 손실).

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$port = 8790
$url  = "http://127.0.0.1:$port/"
$py   = Join-Path $root '.venv\Scripts\python.exe'

function Fail([string]$message) {
    Write-Host $message
    (New-Object -ComObject WScript.Shell).Popup($message, 0, 'MarketView', 0x10) | Out-Null
    exit 1
}

# 닫힌 루프백 포트로 connect 하면 거절이 즉시 오지 않고 2초씩 날아가므로 리슨 목록부터 본다.
function Test-Listening {
    $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    return [bool]($listeners | Where-Object { $_.Port -eq $port })
}

# 상태 코드는 따지지 않는다: 오류를 내는 서버 위에 두 번째 서버를 띄우면 포트 충돌만 난다.
function Test-Server([int]$seconds) {
    $deadline = [datetime]::Now.AddSeconds($seconds)
    while ($true) {
        if (Test-Listening) {
            try {
                $request = [net.webrequest]::Create($url)
                $request.Timeout = 2000
                $request.Proxy = $null
                $response = $request.GetResponse()
                $reader = New-Object IO.StreamReader($response.GetResponseStream())
                $body = $reader.ReadToEnd()
                $reader.Close(); $response.Close()
                # 포트를 다른 프로그램이 쓰고 있으면 그 화면을 MarketView 로 착각하지 않도록 첫 화면 제목까지 본다
                if ($body -match '<title>MarketView</title>') { return $true }
            } catch {
                # 오류를 내는 MarketView(uvicorn) 위에 두 번째 서버를 띄우지 않는다. 다른 프로그램의 오류 응답은 해당하지 않는다.
                if ($_.Exception.Response -and "$($_.Exception.Response.Headers['Server'])" -match 'uvicorn') { return $true }
            }
        }
        if ([datetime]::Now -ge $deadline) { return $false }
        Start-Sleep -Milliseconds 100
    }
}

if (-not (Test-Path $py)) {
    Fail @"
가상환경을 찾지 못했습니다.

  $py

만드는 법:
  cd $root
  py -3.14 -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
"@
}

if (-not (Test-Server 0)) {
    Start-Process -FilePath $py `
        -ArgumentList '-m', 'uvicorn', 'marketview.app:app', '--host', '127.0.0.1', '--port', $port `
        -WorkingDirectory $root `
        -WindowStyle Minimized

    # 첫 기동은 마이그레이션·수집 스레드 시작 때문에 조금 걸릴 수 있다
    if (-not (Test-Server 40)) {
        Fail @"
서버가 40초 안에 응답하지 않았습니다.

아래를 직접 실행해 오류를 확인하세요.
  cd $root
  .venv\Scripts\python -m uvicorn marketview.app:app --host 127.0.0.1 --port $port
"@
    }
}

$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList "--app=$url", '--window-size=1920,1080', '--window-position=0,0'
} else {
    Start-Process $url
}
