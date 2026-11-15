; Instalador para Windows do Sistema de Consolidação de Carteiras (RNF-009).
; Compilado por construir.ps1:  ISCC /DVersao=1.0.0 /DOrigem=<pasta build> instalador.iss
;
; Instala por usuário, sem pedir administrador. O programa vai para
; %LOCALAPPDATA%\Programs\ConsolidacaoCarteiras; os dados ficam separados em
; %LOCALAPPDATA%\ConsolidacaoCarteiras e sobrevivem a atualizações.

#define NomeApp "Consolidação de Carteiras"
#ifndef Versao
  #define Versao "1.0.0"
#endif
#ifndef Origem
  #define Origem "build"
#endif
#define Pythonw "{app}\python\pythonw.exe"
#define Iniciador "{app}\app\distribuicao\iniciar_carteira.py"
#define IconeApp "{app}\app\distribuicao\carteira.ico"

[Setup]
AppId={{2F83667E-22B6-4505-8D60-DC2707B7E6EC}
AppName={#NomeApp}
AppVersion={#Versao}
AppVerName={#NomeApp} {#Versao}
AppPublisher=TCC Engenharia de Software · Universidade Positivo · 2026
DefaultDirName={localappdata}\Programs\ConsolidacaoCarteiras
DefaultGroupName={#NomeApp}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=saida
OutputBaseFilename=Instalar-ConsolidacaoCarteiras-{#Versao}
SetupIconFile=carteira.ico
UninstallDisplayIcon={#IconeApp}
UninstallDisplayName={#NomeApp}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
CloseApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "demo"; Description: "Carregar dados de demonstração (contas e carteira fictícias)"; GroupDescription: "Dados iniciais:"; Check: SemBancoExistente
Name: "iniciarwindows"; Description: "Iniciar com o Windows, em segundo plano (coleta de cotações às 19h30 e backup às 22h30)"; GroupDescription: "Automação:"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#Origem}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#NomeApp}"; Filename: "{#Pythonw}"; Parameters: """{#Iniciador}"""; WorkingDir: "{app}\app"; IconFilename: "{#IconeApp}"; Comment: "Abre o sistema no navegador"
Name: "{autoprograms}\Encerrar {#NomeApp}"; Filename: "{#Pythonw}"; Parameters: """{#Iniciador}"" --encerrar"; WorkingDir: "{app}\app"; IconFilename: "{#IconeApp}"; Comment: "Encerra o servidor local"
Name: "{autodesktop}\{#NomeApp}"; Filename: "{#Pythonw}"; Parameters: """{#Iniciador}"""; WorkingDir: "{app}\app"; IconFilename: "{#IconeApp}"; Tasks: desktopicon
Name: "{userstartup}\{#NomeApp}"; Filename: "{#Pythonw}"; Parameters: """{#Iniciador}"" --segundo-plano"; WorkingDir: "{app}\app"; IconFilename: "{#IconeApp}"; Tasks: iniciarwindows

[Run]
Filename: "{#Pythonw}"; Parameters: """{#Iniciador}"""; WorkingDir: "{app}\app"; Description: "Abrir {#NomeApp} agora"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{#Pythonw}"; Parameters: """{#Iniciador}"" --encerrar"; WorkingDir: "{app}\app"; Flags: runhidden waituntilterminated; RunOnceId: "EncerrarServidor"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function PastaUsuario: String;
begin
  Result := ExpandConstant('{localappdata}\ConsolidacaoCarteiras');
end;

function SemBancoExistente: Boolean;
begin
  Result := not FileExists(PastaUsuario + '\data\carteira.db');
end;

procedure EncerrarInstancia(const Pasta: String);
var
  Codigo: Integer;
begin
  if FileExists(Pasta + '\python\pythonw.exe') then
    Exec(Pasta + '\python\pythonw.exe',
         '"' + Pasta + '\app\distribuicao\iniciar_carteira.py" --encerrar',
         Pasta + '\app', SW_HIDE, ewWaitUntilTerminated, Codigo);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  EncerrarInstancia(ExpandConstant('{app}'));
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('demo') and SemBancoExistente then
  begin
    ForceDirectories(PastaUsuario);
    SaveStringToFile(PastaUsuario + '\carregar_demonstracao', '', False);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DirExists(PastaUsuario) and not UninstallSilent then
    if MsgBox('Deseja apagar também os seus dados (carteiras, backups, chaves e registros)?' + #13#10#13#10 +
              'Escolha "Não" para mantê-los e reutilizá-los numa nova instalação.' + #13#10 +
              'Pasta: ' + PastaUsuario, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(PastaUsuario, True, True, True);
end;
