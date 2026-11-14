<#
.SYNOPSIS
  Gera o instalador para Windows do Sistema de Consolidação de Carteiras.

.DESCRIPTION
  1. Baixa o Python embutido oficial (python.org), com verificação SHA-256.
  2. Instala as dependências de execução (requirements.txt + pystray) dentro dele.
  3. Copia o código da aplicação, sem dados, testes nem artefatos locais.
  4. Pré-compila os módulos e confere se todas as importações funcionam.
  5. Compila o instalador com o Inno Setup (ISCC.exe).

  Resultado: distribuicao\saida\Instalar-ConsolidacaoCarteiras-<versão>.exe

  Requisitos na máquina de build: Python 3.14 (launcher "py") e Inno Setup 6.
  Na máquina do usuário final não é preciso nada além do Windows 10/11 64 bits.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File distribuicao\construir.ps1
  powershell -ExecutionPolicy Bypass -File distribuicao\construir.ps1 -SemInstalador
#>
param(
    [string]$Versao = "1.0.0",
    [switch]$SemInstalador
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Dist = $PSScriptRoot
$Raiz = Split-Path -Parent $Dist
$Build = Join-Path $Dist "build"
$Cache = Join-Path $Dist "cache"
$Saida = Join-Path $Dist "saida"

$PyVersao = "3.14.3"
$PyTag = "314"
$PyZip = "python-$PyVersao-embed-amd64.zip"
$PyUrl = "https://www.python.org/ftp/python/$PyVersao/$PyZip"
$PySha256 = "ad4961a479dedbeb7c7d113253f8db1b1935586b73c27488712beec4f2c894e6"

function Etapa($texto) { Write-Host "`n>> $texto" -ForegroundColor Cyan }

function Executar($exe, [string[]]$argumentos) {
    & $exe @argumentos
    if ($LASTEXITCODE -ne 0) { throw "Falhou ($LASTEXITCODE): $exe $($argumentos -join ' ')" }
}

Etapa "Conferindo ferramentas"
$PyHost = (& py -$($PyVersao.Substring(0, 4)) -c "import sys; print(sys.executable)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $PyHost) { throw "Python $($PyVersao.Substring(0, 4)) não encontrado (py launcher)." }
Write-Host "   Python de build: $PyHost"
$Iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $SemInstalador -and -not $Iscc) {
    throw "Inno Setup 6 não encontrado. Instale com: winget install JRSoftware.InnoSetup --scope user"
}

Etapa "Limpando $Build"
if (Test-Path $Build) { Remove-Item $Build -Recurse -Force }
New-Item -ItemType Directory -Force $Build, $Cache, $Saida | Out-Null

Etapa "Python embutido $PyVersao"
$ZipLocal = Join-Path $Cache $PyZip
if (-not (Test-Path $ZipLocal)) {
    Write-Host "   baixando $PyUrl"
    Invoke-WebRequest -UseBasicParsing $PyUrl -OutFile $ZipLocal
}
$hash = (Get-FileHash $ZipLocal -Algorithm SHA256).Hash.ToLower()
if ($hash -ne $PySha256) {
    Remove-Item $ZipLocal -Force
    throw "SHA-256 divergente para $PyZip (obtido $hash). Arquivo descartado."
}
Write-Host "   SHA-256 conferido"
$PyDir = Join-Path $Build "python"
Expand-Archive $ZipLocal -DestinationPath $PyDir
$Pth = Join-Path $PyDir "python$PyTag._pth"
Set-Content -Path $Pth -Encoding ascii -Value @("python$PyTag.zip", ".", "Lib\site-packages", "import site")

Etapa "Dependências de execução"
$SitePackages = Join-Path $PyDir "Lib\site-packages"
$Travadas = Join-Path $Dist "requisitos-instalador.txt"
if (Test-Path $Travadas) {
    Write-Host "   versões travadas: $Travadas"
    $origemReq = @("--no-deps", "-r", $Travadas)
} else {
    Write-Host "   sem versões travadas: resolvendo requirements.txt (rode os testes e versione o resultado)"
    $origemReq = @("-r", (Join-Path $Raiz "requirements.txt"), "pystray>=0.19,<1")
}
Executar $PyHost (@("-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
    "--only-binary=:all:", "--target", $SitePackages) + $origemReq)
if (-not (Test-Path $Travadas)) {
    $cab = @("# Versões exatas embarcadas no instalador. Gerado por construir.ps1 em $(Get-Date -Format yyyy-MM-dd);",
             "# valide com a suíte de testes antes de versionar.")
    $congeladas = & $PyHost -m pip list --path $SitePackages --format=freeze --disable-pip-version-check
    [IO.File]::WriteAllLines($Travadas, ($cab + $congeladas), (New-Object Text.UTF8Encoding $false))
}

Etapa "Ícone"
$Icone = Join-Path $Dist "carteira.ico"
if (-not (Test-Path $Icone)) { Executar $PyHost @((Join-Path $Dist "gerar_icone.py")) }

Etapa "Código da aplicação"
$AppDir = Join-Path $Build "app"
$excluirDirs = @("data", "tests", "certs", ".claude", ".pytest_cache", "__pycache__", "htmlcov",
    ".venv", "venv", ".git", "telas", (Join-Path $Dist "build"), (Join-Path $Dist "cache"),
    (Join-Path $Dist "saida"))
$excluirArqs = @(".coverage", "*.pyc", ".env", "*.local", ".mcp.json", "settings.local.json")
& robocopy $Raiz $AppDir /E /NFL /NDL /NJH /NJS /NP /XD @excluirDirs /XF @excluirArqs | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy falhou ($LASTEXITCODE)" }
foreach ($desnecessario in @("Dockerfile", "docker-compose.yml", "docker-entrypoint.sh", ".dockerignore",
        "run.sh", "run.bat", "mcp_server.py", "requirements-dev.txt", "requirements-mcp.txt",
        ".gitignore", ".gitattributes", "distribuicao\construir.ps1", "distribuicao\instalador.iss",
        "distribuicao\gerar_icone.py", "distribuicao\requisitos-instalador.txt")) {
    $alvo = Join-Path $AppDir $desnecessario
    if (Test-Path $alvo) { Remove-Item $alvo -Force }
}

Etapa "Pré-compilando e conferindo importações"
$PyEmb = Join-Path $PyDir "python.exe"
Executar $PyEmb @("-m", "compileall", "-q", "-j", "0", $AppDir)
Executar $PyEmb @("-c", @"
import importlib, sys
mods = ['streamlit', 'pandas', 'plotly', 'openpyxl', 'sqlalchemy', 'reportlab', 'apscheduler',
        'segno', 'bcrypt', 'jwt', 'cryptography', 'requests', 'yfinance', 'pystray', 'PIL', 'sqlite3']
for m in mods:
    importlib.import_module(m)
sys.path.insert(0, r'$AppDir')
import config, src.security, src.calc.consolidacao, src.agendador
print('   importações OK:', len(mods), 'pacotes + aplicação')
"@)
$tamanho = (Get-ChildItem $Build -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("   pasta pronta: {0:N0} MB em {1}" -f $tamanho, $Build)

if ($SemInstalador) { Write-Host "`nPasta portátil pronta (sem instalador)."; exit 0 }

Etapa "Compilando o instalador (Inno Setup)"
Executar $Iscc @("/Q", "/DVersao=$Versao", "/DOrigem=$Build", "/O$Saida", (Join-Path $Dist "instalador.iss"))
$exe = Get-ChildItem $Saida -Filter "Instalar-ConsolidacaoCarteiras-$Versao.exe" | Select-Object -First 1
$sha = (Get-FileHash $exe.FullName -Algorithm SHA256).Hash
Write-Host ("`nInstalador: {0} ({1:N0} MB)`nSHA-256: {2}" -f $exe.FullName, ($exe.Length / 1MB), $sha) -ForegroundColor Green
