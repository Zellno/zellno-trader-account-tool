# Zellno Trader Account Tool

Ferramenta administrativa externa para auditar contas bancárias e licenças de jogadores usadas pelo TraderPlus no Zellno DayZ Server.

> [!IMPORTANT]
> Projeto comunitário e não oficial. Não possui afiliação, aprovação ou suporte do
> TraderPlus, DayZ, Bohemia Interactive ou qualquer empresa de hospedagem.
> Faça backups e teste o procedimento em ambiente controlado antes de operar dados reais.

## Estado atual: versão 0.8.0, auditoria econômica com conciliação administrativa

Esta versão:

- cruza opcionalmente snapshots com o `remote-audit.jsonl` da própria ferramenta;
- confirma alterações administrativas somente por SteamID64, arquivo, intervalo e hashes exatos;
- mantém registros inválidos, ambíguos ou incompatíveis fora da conciliação;
- evita classificar como alerta uma movimentação cuja implantação administrativa foi comprovada;

- compara cronologicamente snapshots locais sem abrir conexão FTP;
- valida os hashes declarados antes de incluir um snapshot na auditoria;
- exclui contas inválidas das métricas e explica cada exclusão;
- calcula saldo total, médio, mediano, maior saldo e concentração no snapshot mais recente;
- calcula variações de saldo e mudanças de licenças por SteamID64;
- sinaliza aumentos positivos exatos ou múltiplos de valores configuráveis;
- gera relatórios TXT, JSON e CSV com manifesto SHA-256;

- lê cópias locais de `Account_<SteamID64>.json`;
- localiza contas por nome, SteamID64 ou arquivo;
- valida a estrutura e a identidade da conta;
- detecta divergência entre o nome do arquivo e o SteamID64 interno;
- mostra saldo e limite bancário;
- compara licenças da conta com `TraderPlusGeneralConfig.json`;
- não mostra nem interpreta seguros;
- consultas e simulações não escrevem, renomeiam, removem ou enviam arquivos;
- snapshots FTP permanecem limitados a listagem (`NLST`) e download (`RETR`);
- gera planos de alteração totalmente em memória;
- simula perfis e operações individuais mostrando estado atual e proposto;
- aplica planos somente a cópias locais mediante `--apply` e confirmação do SteamID64;
- cria backup antes de substituir a cópia local;
- valida arquivo temporário e resultado final;
- usa substituição atômica no mesmo sistema de arquivos;
- registra aplicações em `audit.jsonl`;
- restaura saldo e licenças de backups sem tocar em seguros ou campos desconhecidos;
- baixa snapshots novos sem sobrescrever snapshots anteriores;
- valida configuração, contas, identidades e hashes após o download;
- grava manifesto local sem usuário ou senha FTP;
- marca snapshots online como informativos e não confiáveis para edição;
- calcula confiança individual para a conta escolhida em um snapshot parado;
- mantém contas inconsistentes bloqueadas sem impedir outra conta válida;
- prepara pacote local com original, proposta, diff, hashes, manifesto e auditoria;
- preserva semanticamente seguros e todos os campos desconhecidos na proposta;
- nunca altera o snapshot usado como origem;
- a preparação do pacote, isoladamente, não envia qualquer arquivo ao FTP;
- valida novamente todo o pacote antes de qualquer conexão de implantação;
- compara o hash remoto atual com o original do snapshot antes do upload;
- cria backup local exato do estado remoto imediatamente anterior;
- envia a proposta primeiro com nome temporário e lê o conteúdo de volta;
- renomeia o original para um backup remoto antes da ativação;
- verifica o backup remoto antes de ativar a proposta;
- valida o arquivo final por nova leitura e executa rollback em caso de divergência;
- registra intenção, sucesso e falhas de implantação em auditoria externa ao pacote.

Não coloque contas reais, senhas FTP ou arquivos locais de configuração no repositório.

## Privacidade e publicação

Snapshots, pacotes de implantação, relatórios econômicos, backups e auditorias podem
conter SteamID64, nomes de jogadores e dados administrativos. Esses artefatos são locais
e não devem ser enviados ao GitHub. O `.gitignore` inclui proteções para os nomes e
diretórios usados pela ferramenta, mas o operador continua responsável por revisar cada
commit antes de publicá-lo.

Nunca informe a senha FTP em argumentos, arquivos de configuração, relatórios, issues ou
logs públicos. A ferramenta solicita a senha interativamente e não a armazena.

## Instalação para desenvolvimento

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Também é possível executar diretamente sem instalar:

```bash
PYTHONPATH=src python -m zellno_trader --help
```

## Uso

Considere uma pasta local com esta estrutura:

```text
auditoria/
├── Account_7656119XXXXXXXXXX.json
└── TraderPlusGeneralConfig.json
```

Listar todas as contas:

```bash
zellno-trader \
  --accounts-dir ./auditoria \
  --general-config ./auditoria/TraderPlusGeneralConfig.json \
  account list
```

Buscar por nome:

```bash
zellno-trader \
  --accounts-dir ./auditoria \
  --general-config ./auditoria/TraderPlusGeneralConfig.json \
  account show --name Zellno
```

Buscar por SteamID64:

```bash
zellno-trader \
  --accounts-dir ./auditoria \
  --general-config ./auditoria/TraderPlusGeneralConfig.json \
  account show --steamid 7656119XXXXXXXXXX
```

Inspecionar explicitamente um arquivo quando uma busca for ambígua:

```bash
zellno-trader \
  --accounts-dir ./auditoria \
  --general-config ./auditoria/TraderPlusGeneralConfig.json \
  account show --file Account_7656119XXXXXXXXXX.json
```

## Auditoria econômica de snapshots

Mostrar uma auditoria local no terminal, sem criar arquivos e sem abrir FTP:

```bash
zellno-trader economy audit --snapshots-dir ./snapshots
```

Os sinais padrão são `5000`, `33250` e `150000`. Para substituí-los:

```bash
zellno-trader economy audit --snapshots-dir ./snapshots \
  --signal 5000 --signal 33250 --signal 150000
```

Gerar também relatórios reutilizáveis:

```bash
zellno-trader economy audit \
  --snapshots-dir ./snapshots \
  --destination ./economy-reports
```

Conciliar alterações realizadas pela própria ferramenta:

```bash
zellno-trader economy audit \
  --snapshots-dir ./snapshots \
  --remote-audit ./remote-state/remote-audit.jsonl \
  --destination ./economy-reports
```

A conciliação não confia apenas no texto do log: SteamID64, nome do arquivo, horário e
hashes anterior e posterior precisam coincidir exatamente com os dois snapshots.

Cada snapshot tem sua integridade conferida antes da leitura. Contas inválidas ou
inconsistentes aparecem no relatório, mas não participam de totais, médias ou
variações. Alertas são apenas indícios de variação líquida, não prova da origem do
dinheiro.

## Códigos de saída

| Código | Significado |
|---:|---|
| 0 | consulta concluída sem erros estruturais |
| 1 | conta não encontrada ou argumento inválido |
| 2 | conta inválida, inconsistente ou busca ambígua |

## Planos em dry-run

Todos os comandos abaixo apenas simulam. Eles não criam nem modificam JSON.

Perfil Jogador normal, com saldo zero e nenhuma licença:

```bash
zellno-trader --accounts-dir ./auditoria --general-config ./auditoria/TraderPlusGeneralConfig.json \
  account plan profile normal --file Account_7656119XXXXXXXXXX.json
```

Perfil Teste administrativo com estado final exato:

```bash
zellno-trader --accounts-dir ./auditoria --general-config ./auditoria/TraderPlusGeneralConfig.json \
  account plan profile test --file Account_7656119XXXXXXXXXX.json \
  --balance 1000000 --licence "Bob Licence" --licence "Mason Licence"
```

Operações individuais:

```bash
zellno-trader ... account plan balance set --file Account_7656119XXXXXXXXXX.json --amount 500000
zellno-trader ... account plan balance zero --file Account_7656119XXXXXXXXXX.json
zellno-trader ... account plan licence add --file Account_7656119XXXXXXXXXX.json --licence "Bob Licence"
zellno-trader ... account plan licence remove --file Account_7656119XXXXXXXXXX.json --licence "Bob Licence"
zellno-trader ... account plan licence clear --file Account_7656119XXXXXXXXXX.json
```

## Aplicação local

Acrescente `--apply` ao final de qualquer comando de plano. A ferramenta mostra novamente o plano e exige que o SteamID64 seja digitado exatamente:

```bash
zellno-trader --accounts-dir ./auditoria --general-config ./auditoria/TraderPlusGeneralConfig.json \
  account plan profile normal --file Account_7656119XXXXXXXXXX.json --apply
```

Sem `--apply`, todo comando continua sendo `dry-run`.

Por padrão, a ferramenta cria:

```text
<accounts-dir>/.zellno-trader-account-tool/
├── audit.jsonl
└── backups/
```

Uma aplicação local bem-sucedida não envia nada ao servidor. O operador ainda precisa tratar manualmente o arquivo resultante e manter o servidor parado antes de qualquer substituição remota.

## Backups e restauração

Listar backups:

```bash
zellno-trader --accounts-dir ./auditoria --general-config ./auditoria/TraderPlusGeneralConfig.json \
  backup list
```

Simular restauração:

```bash
zellno-trader --accounts-dir ./auditoria --general-config ./auditoria/TraderPlusGeneralConfig.json \
  backup restore --file Account_7656119XXXXXXXXXX.json --backup NOME_EXATO_DO_BACKUP
```

Aplicar restauração local:

```bash
zellno-trader --accounts-dir ./auditoria --general-config ./auditoria/TraderPlusGeneralConfig.json \
  backup restore --file Account_7656119XXXXXXXXXX.json --backup NOME_EXATO_DO_BACKUP --apply
```

A restauração recupera somente saldo e licenças. Seguros e campos desconhecidos do arquivo atual são preservados.

## Barreiras de segurança

- conta inconsistente ou JSON inválido bloqueia planejamento e aplicação;
- saldo fora do intervalo permitido é rejeitado;
- licenças novas precisam existir na configuração ativa;
- links simbólicos não podem ser substituídos;
- confirmação incorreta não cria sequer a pasta de estado;
- falha posterior à substituição provoca restauração automática do original;
- toda restauração cria primeiro outro backup do estado atual;
- FTP simples exige consentimento explícito com `--allow-plain-ftp`;
- senha é solicitada por prompt oculto e nunca gravada;
- snapshots continuam usando somente listagem e download;
- implantação exige `--apply`, `--server-stopped`, `--allow-plain-ftp` e dupla confirmação;
- exclusão remota é limitada exclusivamente a um temporário criado pela própria tentativa;
- o arquivo original nunca é excluído: ele é renomeado para backup remoto;
- divergência entre o snapshot e o estado remoto bloqueia antes do upload.

## Snapshot FTP somente leitura

A 4Netplayers observada não oferece FTP sobre TLS. O uso de FTP simples expõe as credenciais ao transporte da rede e, por isso, exige reconhecimento explícito:

```bash
zellno-trader remote snapshot \
  --host HOST_FTP \
  --port 21 \
  --user USUARIO_FTP \
  --destination ./snapshots \
  --allow-plain-ftp
```

A senha é solicitada sem aparecer na tela. Ela não pode ser passada como argumento e não é incluída no manifesto.

Para um snapshot destinado a uma futura alteração, pare primeiro o servidor e acrescente:

```text
--server-stopped
```

Sem essa declaração, o snapshot será baixado e validado, mas marcado como meramente informativo, porque contas podem mudar enquanto o servidor está online.

Estrutura produzida:

```text
snapshots/
└── snapshot-<data UTC>/
    ├── snapshot-manifest.json
    ├── TraderPlusBankDatabase/
    │   └── Account_<SteamID64>.json
    └── TraderPlusConfig/
        └── TraderPlusGeneralConfig.json
```

O comando de snapshot contém somente as operações FTP necessárias para conectar, autenticar, listar diretórios e baixar arquivos. As operações mutáveis existem isoladamente no fluxo explícito de implantação introduzido na versão 0.6.0.

## Preparação local de implantação

Somente snapshots criados com o servidor parado e a opção `--server-stopped` podem servir de origem. A confiança é avaliada para o arquivo selecionado: outra conta inconsistente permanece bloqueada, mas não contamina uma conta válida.

Simular o perfil Jogador normal sem criar arquivos:

```bash
zellno-trader deployment prepare \
  --snapshot ./snapshots/snapshot-<data UTC> \
  --file Account_7656119XXXXXXXXXX.json \
  --destination ./deployments \
  normal
```

Criar o pacote após confirmação do SteamID64:

```bash
zellno-trader deployment prepare \
  --snapshot ./snapshots/snapshot-<data UTC> \
  --file Account_7656119XXXXXXXXXX.json \
  --destination ./deployments \
  normal --create
```

Perfil Teste administrativo:

```bash
zellno-trader deployment prepare \
  --snapshot ./snapshots/snapshot-<data UTC> \
  --file Account_7656119XXXXXXXXXX.json \
  --destination ./deployments \
  test --balance 1000000 \
  --licence "Bob Licence" --licence "Mason Licence" --create
```

Estrutura produzida:

```text
deployments/
└── deployment-<data UTC>-<SteamID64>/
    ├── deployment-manifest.json
    ├── audit.jsonl
    ├── changes.diff
    ├── original/Account_<SteamID64>.json
    └── proposed/Account_<SteamID64>.json
```

O estado do pacote é `prepared_not_deployed`. A presença do arquivo proposto não significa que ele tenha sido enviado ou aplicado ao servidor.

## Implantação FTP controlada

Validar o pacote e mostrar o plano sem abrir conexão FTP:

```bash
zellno-trader remote deploy \
  --package ./deployments/deployment-<data>-<SteamID64> \
  --host HOST_FTP \
  --user USUARIO_FTP \
  --state-dir ./remote-state
```

Para executar uma implantação real, o servidor deve estar parado durante toda a operação:

```bash
zellno-trader remote deploy \
  --package ./deployments/deployment-<data>-<SteamID64> \
  --host HOST_FTP \
  --user USUARIO_FTP \
  --state-dir ./remote-state \
  --allow-plain-ftp \
  --server-stopped \
  --apply
```

Antes de solicitar a senha, a ferramenta exige o SteamID64 exato e a palavra `IMPLANTAR`. A senha é lida por prompt oculto e nunca é gravada.

Sequência remota:

1. baixar novamente a conta e comparar seu hash com o pacote;
2. criar e validar backup local;
3. registrar a intenção na auditoria;
4. enviar e reler o arquivo temporário;
5. renomear e verificar o original como backup remoto;
6. ativar a proposta por renomeação;
7. reler e conferir o hash final;
8. registrar sucesso ou executar rollback quando necessário.

O backup remoto permanece no diretório bancário com um nome que não corresponde ao padrão `Account_*.json`, portanto não é carregado como uma conta pelo fluxo de snapshots da ferramenta.

## Testes

```bash
python -m unittest discover -s tests -v
```

Os testes usam dados fictícios e não incluem contas do servidor.

## Escopo futuro

A versão 0.8.0 representa o escopo externo estável da ferramenta. Novos recursos serão
avaliados somente diante de necessidades operacionais concretas, preservando a segurança
e a compatibilidade dos fluxos já homologados.

## Licença

Distribuído sob a licença MIT. Consulte [`LICENSE`](LICENSE).
