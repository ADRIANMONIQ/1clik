# AGENTS.md — 1Clik

- Manter o 1Clik como aplicação independente do CRM LOJA.
- Não ler, alterar ou versionar o banco do CRM.
- Não versionar banco local, contatos, campanhas reais, uploads, sessões,
  tokens, chaves ou perfis do Chrome.
- Preservar os modos WhatsApp Web e API Oficial da Meta e suas rotinas de fila.
- Testes devem usar respostas simuladas e nunca disparar mensagens reais sem
  autorização explícita.
- Não usar `git reset --hard`, `git clean` ou `git push --force`.
- Branch principal somente após revisão; usar branch de consolidação.
