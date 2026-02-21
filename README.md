# SubManager — Gerenciador de Assinaturas Telegram

Sistema para gerar links de acesso com validade para grupos do Telegram.
O lead compra no Discord, recebe o link, entra no grupo e e removido automaticamente apos o periodo contratado.

---

## Fluxo

```
Lead compra no Discord
       │
       ▼
Bot Discord entrega o link (ex: https://seusite.com/acesso/TOKEN)
       │
       ▼
Lead clica → pagina de redirect → abre bot Telegram (/start TOKEN)
       │
       ▼
Bot registra o usuario, gera link de convite (1 uso) do grupo e envia
       │
       ▼
Lead entra no grupo
       │
       ▼
Scheduler verifica a cada X minutos → remove quem expirou
```

---

## Configuracao

### 1. Criar o bot no Telegram
1. Abra o Telegram e converse com [@BotFather](https://t.me/BotFather)
2. `/newbot` → siga as instrucoes → copie o token
3. Adicione o bot como **administrador** no seu grupo com permissao de "Banir usuarios"

### 2. Descobrir o ID do grupo
- Adicione [@userinfobot](https://t.me/userinfobot) no grupo, envie qualquer mensagem
- Ou use: `https://api.telegram.org/bot<TOKEN>/getUpdates` e veja o `chat.id`
- O ID de grupo começa com `-100...`

### 3. Editar config.py
```python
TELEGRAM_BOT_TOKEN = "1234567890:AAF..."   # token do BotFather
TELEGRAM_GROUP_ID  = -1001234567890        # ID do grupo (numero negativo)
ADMIN_USER = "admin"                       # usuario do painel web
ADMIN_PASS = "sua_senha_forte"             # senha do painel web
BASE_URL   = "https://seusite.com"         # URL publica do servidor
```

### 4. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 5. Rodar
```bash
python main.py
```

O painel fica disponivel em `http://localhost:5000` (ou na porta configurada).

---

## Como usar no dia a dia

### Gerar links para o estoque do Discord
1. Acesse o painel → **Gerar Links**
2. Defina a duracao (ex: 30 dias) e a quantidade
3. Copie os links gerados → cole no estoque do produto no bot do Discord

### O que acontece quando o lead compra
1. O bot Discord envia o link: `https://seusite.com/acesso/TOKEN`
2. O lead clica, ve uma pagina com o botao "Abrir no Telegram"
3. O bot Telegram recebe `/start TOKEN`, registra o usuario e envia o link do grupo
4. Apos X dias o scheduler remove o usuario automaticamente

### Revogar manualmente
- Painel → **Todos os Links** → botao laranja (pessoa com "-") na linha do usuario

---

## Estrutura de arquivos

```
painel discord/
├── main.py          # ponto de entrada (sobe tudo)
├── app.py           # painel web Flask
├── bot.py           # bot Telegram
├── scheduler.py     # loop de remocao de expirados
├── database.py      # SQLite
├── config.py        # configuracoes
├── requirements.txt
└── templates/
    ├── base.html
    ├── login.html
    ├── dashboard.html
    ├── generate.html
    ├── links.html
    ├── redirect.html   # pagina que o lead ve ao clicar no link
    └── invalid.html
```

---

## Expondo para a internet (para o Discord enviar links publicos)

- **Teste local**: use [ngrok](https://ngrok.com/) → `ngrok http 5000` → copie a URL HTTPS no `BASE_URL`
- **Producao**: VPS com Nginx + SSL (certbot), rode com `python main.py` ou via systemd/PM2

---

## Variaveis de ambiente (alternativa ao config.py)

| Variavel                  | Descricao                          |
|---------------------------|------------------------------------|
| `TELEGRAM_BOT_TOKEN`      | Token do bot                       |
| `TELEGRAM_GROUP_ID`       | ID numerico do grupo               |
| `SECRET_KEY`              | Chave secreta Flask                |
| `ADMIN_USER`              | Usuario do painel                  |
| `ADMIN_PASS`              | Senha do painel                    |
| `BASE_URL`                | URL publica do servidor            |
| `PORT`                    | Porta do Flask (padrao: 5000)      |
| `CHECK_INTERVAL_MINUTES`  | Frequencia do scheduler (padrao 30)|
