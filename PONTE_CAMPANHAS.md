# Ponte de campanhas para o 1Clik

Esta ponte recebe o resultado de um coletor Promobank ou Promosys, normaliza os
telefones e disponibiliza os contatos nas telas de importação. Ela não inicia
disparos: a campanha criada pelo resultado entra como `rascunho`.

## Entrega do coletor

Envie `POST /api/campaign-imports/result` para o painel local, com o cabeçalho
`X-1Clik-Key` contendo a chave interna exibida em **Conexões**.

Exemplo de corpo JSON:

```json
{
  "source": "promobank",
  "campaign_name": "CAMPANHA INSS - PARTE 1",
  "status": "concluida",
  "leads_total": 10,
  "rows": [
    {
      "NOME": "MARIA SILVA",
      "CPF": "00000000000",
      "CONTATOS": "91999990000; 91988880000",
      "ESPÉCIE": "42"
    }
  ]
}
```

Resposta esperada:

```json
{"id": 1, "status": "concluida", "contacts_total": 2}
```

## Fluxo visual

1. O resultado aparece na tela da origem com totais de leads e contatos.
2. **Baixar Excel** exporta a lista normalizada.
3. **Copiar contatos para disparo** carrega o lote no criador de campanhas.
4. O operador escolhe conexão, modelo, mensagem e programação.
5. **Importar e preparar campanha** cria somente o rascunho para conferência.

## Regras de segurança

- O endpoint aceita apenas `promobank` ou `promosys`.
- A chave interna é obrigatória e comparada de forma segura.
- Telefones inválidos são ignorados e telefones repetidos são deduplicados.
- A seleção é vinculada ao usuário autenticado.
- Nenhuma mensagem é enviada pela ponte.
