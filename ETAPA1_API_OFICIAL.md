# Etapa 1 — API Oficial da Meta

## Arquivos

- `app.py`: interface, rotas administrativas, criação e controle das campanhas.
- `meta_api.py`: criptografia, cliente Graph API, validação, templates e envio oficial.
- `meta_schema.py`: migração incremental do SQLite sem apagar dados.
- `campaign_worker.py`: trabalhador persistente da fila.
- `iniciar_fila_oficial.bat`: inicialização manual do trabalhador.
- `tests/test_meta_etapa1.py`: testes totalmente simulados.
- `scripts/validate_official_api_menu.py`: auditoria local de esquema e segredos.
- `README.md`: operação e roteiro manual.

## Novas tabelas

- `meta_official_templates`: templates oficiais separados dos modelos locais.
- `meta_campaign_send_attempts`: um registro persistente por contato e campanha.
- `meta_audit_logs`: conexão, credenciais, sincronização, controles e erros.

## Colunas adicionadas

### whatsapp_channels

`meta_waba_id`, `meta_app_secret`, `meta_access_token_enc`, `meta_app_secret_enc`, `meta_token_masked`, `meta_connection_status`, `meta_connection_details_json`, `meta_last_tested_at`.

### whatsapp_campaigns

`official_template_id`, `template_language`, `variable_map_json`, `interval_seconds`, `max_sends`, `scheduled_at`, `started_at`, `completed_at`, `cancel_requested`, `preview_text`.

### whatsapp_campaign_items

`original_phone`, `normalized_phone`, `variables_json`, `attempt_count`, `last_http_status`, `last_error`, `wamid`, `last_attempt_at`.

### app_users

`role`, com o usuário existente mantido como `master`.

## Criptografia

Access Token e App Secret são criptografados com Fernet. O SQLite recebe somente os campos terminados em `_enc`; os campos antigos em texto puro são limpos durante a migração. A chave fica em `secrets/meta_credentials.key`, fora do banco.

## Estados e controles

Campanha: `rascunho`, `agendada`, `em processamento`, `pausada`, `concluída`, `cancelada`, `concluída com falhas`.

Item: `pendente`, `processando`, `enviado`, `falhou`, `ignorado`, `cancelado`.

Erros temporários recebem até três tentativas. Erros permanentes não são repetidos. Uma tentativa interrompida depois de entrar em `processando` não é reenviada automaticamente, evitando duplicação quando o resultado da Meta for desconhecido.

## Fora desta etapa

Webhook, entrada de mensagens, entregue, lido, respondido, mídia oficial e inscrição automática no WABA permanecem fora da Etapa 1.
