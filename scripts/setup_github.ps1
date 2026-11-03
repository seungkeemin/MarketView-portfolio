# GitHub 연결 (Phase 8). 사용자가 직접 실행한다 — 로그인·저장소 생성·Secrets 는 Claude 가 대신하지 않는다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup_github.ps1
#
# 하는 일 (각 단계에서 멈추고 확인한다):
#   1. gh 로그인 확인 (없으면 브라우저 로그인 시작)
#   2. 비공개 저장소 MarketView 생성 + 현재 코드 push  (공개로 만들어졌으면 즉시 중단)
#   3. .env 의 ECOS_API_KEY / FRED_API_KEY / KRX_AUTH_KEY 를 GitHub Secrets 로 등록 (값은 화면에 출력하지 않는다)
#   4. 클라우드 수집 첫 실행(daily)을 시작하고 결과를 지켜본다
#
# 텔레그램 값(TELEGRAM_*)과 세션은 올리지 않는다. 몇 번을 다시 실행해도 안전하다(이미 있는 것은 건너뛴다).

# 'Stop' 이 아니다: Windows PowerShell 5.1 은 네이티브 명령(gh/git)이 stderr 로 한 줄만 써도 예외로 바꿔 중단한다.
# 실패 판단은 아래에서 $LASTEXITCODE 로 명시적으로 한다.
$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)
$RepoName = 'MarketView'
$SecretNames = @('ECOS_API_KEY', 'FRED_API_KEY', 'KRX_AUTH_KEY')

function Step([string]$text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Confirm-Or-Exit([string]$question) {
    $answer = Read-Host "$question [y/N]"
    if ($answer -notmatch '^(y|yes)$') { Write-Host '중단했습니다.'; exit 1 }
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'gh(GitHub CLI)를 찾지 못했습니다. 새 터미널을 열거나 설치를 확인하세요: winget install --id GitHub.cli -e'
}

Step '1/4 GitHub 로그인'
gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    # workflow 권한이 있어야 .github/workflows 를 push 할 수 있다
    gh auth login --web --git-protocol https --scopes 'repo,workflow'
    if ($LASTEXITCODE -ne 0) { throw '로그인에 실패했습니다.' }
}
gh auth setup-git | Out-Null
$login = gh api user --jq .login
if ($LASTEXITCODE -ne 0) { throw 'GitHub 계정 확인에 실패했습니다. gh auth login 을 다시 하세요.' }
Write-Host "로그인 계정: $login"

Step '2/4 비공개 저장소 생성 + push'
$hasOrigin = (git remote) -contains 'origin'
if ($hasOrigin) {
    Write-Host ('이미 origin 이 있습니다: ' + (git remote get-url origin) + ' — 생성은 건너뜁니다.')
} else {
    Write-Host "비공개(private) 저장소 '$RepoName' 를 만들고 현재 브랜치와 히스토리를 올립니다."
    Confirm-Or-Exit '진행할까요?'
    gh repo create $RepoName --private --source . --remote origin --push --description 'MarketView personal market viewer (private)'
    if ($LASTEXITCODE -ne 0) { throw '저장소 생성/푸시에 실패했습니다.' }
}
$private = gh repo view --json isPrivate --jq .isPrivate
if ($private -ne 'true') { throw '저장소가 비공개가 아닙니다. 중단합니다 (GitHub > Settings 에서 비공개로 바꾸세요).' }
Write-Host '저장소 공개 범위: 비공개 확인'
git push origin HEAD 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'git push 에 실패했습니다. 터미널에서 git push origin HEAD 를 직접 실행해 오류를 확인하세요.' }

Step '3/4 Secrets 등록 (값은 출력하지 않음)'
$envFile = Join-Path (Get-Location) '.env'
if (-not (Test-Path $envFile)) { throw '.env 가 없습니다.' }
$values = @{}
foreach ($line in Get-Content $envFile -Encoding UTF8) {
    $t = $line.Trim().TrimStart([char]0xFEFF)
    if ($t -and -not $t.StartsWith('#') -and $t.Contains('=')) {
        $k, $v = $t.Split('=', 2)
        $values[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
    }
}
$existing = @(gh secret list --json name --jq '.[].name')
if ($LASTEXITCODE -ne 0) { throw 'Secrets 목록을 읽지 못했습니다.' }
foreach ($name in $SecretNames) {
    if ($existing -contains $name) { Write-Host "$name : 이미 등록됨 (건너뜀)"; continue }
    if (-not $values[$name]) { Write-Host "$name : .env 에 값이 없어 건너뜀 — 해당 소스는 클라우드에서 '실패'로 표시됩니다" -ForegroundColor Yellow; continue }
    $values[$name] | gh secret set $name      # 표준입력으로 전달: 명령줄·화면에 값이 남지 않는다
    if ($LASTEXITCODE -ne 0) { throw "$name 등록 실패" }
    Write-Host "$name : 등록했습니다"
}

Step '4/4 첫 실행 (daily)'
Confirm-Or-Exit 'GitHub Actions 에서 daily 수집을 한 번 실행할까요?'
gh workflow run collect.yml -f mode=daily
if ($LASTEXITCODE -ne 0) { throw '워크플로 실행 요청에 실패했습니다 (push 직후엔 몇 초 뒤 다시 시도하세요).' }
Start-Sleep -Seconds 8
$runId = gh run list --workflow collect.yml --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $runId --exit-status
Write-Host "`n끝났습니다. 로컬 앱의 '클라우드 동기화' 는 앱이 켜져 있으면 15분 안에 갱신됩니다."
Write-Host '실행 기록: gh run list --workflow collect.yml'
