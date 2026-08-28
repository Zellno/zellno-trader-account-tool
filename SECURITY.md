# Segurança

## Dados que não devem ser publicados

Não inclua em issues, pull requests, commits ou anexos públicos:

- senhas ou credenciais FTP;
- endereços privados de hospedagem;
- contas reais `Account_<SteamID64>.json`;
- snapshots, backups ou pacotes de implantação reais;
- relatórios que identifiquem jogadores;
- arquivos `remote-audit.jsonl` de produção.

Antes de publicar qualquer diagnóstico, substitua SteamID64, nomes, hosts e demais
identificadores por valores fictícios.

## Relato responsável

Se encontrar uma vulnerabilidade que possa provocar perda de dados, alteração remota
indevida ou exposição de credenciais, não publique os detalhes em uma issue aberta.
Entre em contato de forma privada com o mantenedor pelo recurso de relato de segurança
do GitHub, quando disponível.

## Limite de suporte

Esta é uma ferramenta comunitária e não oficial. O operador é responsável por manter
backups, validar o estado do servidor e cumprir todas as confirmações de segurança antes
de uma implantação.
