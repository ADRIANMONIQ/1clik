# 1Clik independente

Aplicativo local separado do CRM com dois modos de envio:

- WhatsApp Web por QR Code;
- WhatsApp Cloud API Oficial da Meta.

## Abrir

Clique duas vezes em `abrir_1clik.bat`. O painel abre em `http://127.0.0.1:8091`.

Na primeira utilização, pressione **Abrir WhatsApp** e escaneie o QR Code. A sessão fica guardada somente em `data/chrome-profile`.

## Separação do CRM

- servidor próprio na porta `8091`;
- navegador controlado próprio na porta `9341`;
- banco próprio em `data/1clik.db`;
- arquivos enviados em `data/uploads`;
- não lê nem altera `omega_clientes.db`.

## Configurações trazidas do Omega

O script `migrate_from_omega.py` copia conexões, modelos, histórico e campanhas para o banco independente. Ele não apaga nem modifica dados do CRM e pode ser executado novamente sem duplicar registros.

## Limites desta primeira versão

### API Oficial — Etapa 1

Estão disponíveis validação de credenciais, sincronização de templates oficiais, campanhas por template, fila persistente, pausa, retomada, cancelamento, retentativas temporárias e histórico individual.

Ficam para a Etapa 2: webhook público, mensagens recebidas, entregue, lido, respondido e mídia pela API Oficial.

## Credenciais e segurança

O Access Token e o App Secret são criptografados com Fernet antes de serem gravados no SQLite. A chave fica fora do banco em `secrets/meta_credentials.key`. Preserve esse arquivo junto com o backup: sem ele, as credenciais não poderão ser recuperadas.

O painel mostra somente a versão mascarada do token. Tokens completos não são gravados nos logs.

## Trabalhador da fila

O trabalhador é iniciado automaticamente junto com `app.py` e também quando uma campanha é iniciada ou retomada. Para iniciá-lo manualmente:

```powershell
python campaign_worker.py
```

Para processar no máximo um item, útil em diagnóstico:

```powershell
python campaign_worker.py --once
```

Somente uma instância processa a fila; as demais encerram ao encontrar o arquivo de bloqueio em uso.

## Teste seguro antes de credenciais reais

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python scripts/validate_official_api_menu.py
```

Os testes usam respostas simuladas e não enviam mensagens reais.

## Roteiro manual da Etapa 1

1. Entre no 1Clik como usuário master.
2. Em **Conexões**, escolha **Com API Oficial — Meta**.
3. Informe Access Token, Phone Number ID, WABA ID e a versão da Graph API.
4. Clique em **Testar conexão**. Nada será salvo se a Meta recusar os dados.
5. Clique em **Salvar conexão validada**.
6. Em **Templates oficiais**, escolha o canal e sincronize.
7. Confirme que o template desejado aparece como `APPROVED`.
8. Importe uma planilha pequena, escolha o template e relacione variáveis no formato `{"1":"nome"}`.
9. Confira a prévia mostrada na campanha em estado `rascunho`.
10. Use um número de teste autorizado pela Meta e clique em **Iniciar**.
11. Acompanhe resultado, HTTP, tentativas, erro e WAMID em **Fila e histórico de envios oficiais**.
12. Teste **Pausar**, **Continuar** e **Cancelar** antes de trabalhar com uma lista real.
