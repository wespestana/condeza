# Sistema de Atualização Cadastral e Mudança de Titularidade de Unidades Condominiais

Trabalho da disciplina Laboratório de Engenharia de Software (Fatec São Paulo, 2026-2), para a administradora fictícia Condeza.
Todos os dados são fictícios (RNF03). O ERP e o aplicativo do condômino são serviços simulados; os e-mails ficam numa caixa local.

## 1. Componentes

Tudo roda em contêineres num único servidor (RNF15), pelo Docker Compose. As portas ficam abertas só no próprio computador (127.0.0.1).

| Serviço | O que é | Endereço no computador |
|---|---|---|
| `app` | Site da Condeza e sistema (Python 3.13, Flask 3.1) | http://127.0.0.1:5080 |
| `worker` | Fila da IA e rotinas periódicas (a cada 5 minutos) | — |
| `mysql` | Banco de dados (MySQL 8.4) | 127.0.0.1:3317 |
| `ollama` | IA local, só no processador (Ollama 0.40.15, modelo Qwen 3.5 4B) | só na rede interna |
| `erp-simulado` | ERP simulado: unidades, responsáveis, débitos, emissão | http://127.0.0.1:5081 (API) |
| `app-simulado` | Aplicativo do condômino simulado: contas, cadastro, contatos | http://127.0.0.1:5082 (API) |
| `mailpit` | Caixa de e-mail local: nada sai do computador | http://127.0.0.1:8036 |

## 2. Requisitos

- Docker com Compose v2 (Docker Desktop no macOS ou Windows; Docker Engine no Linux).
- 4 processadores e 16 GB de memória (a IA usa até 7 GB); cerca de 10 GB livres em disco.
- Internet só na instalação, para baixar as imagens e o modelo de IA (3,3 GB). Em operação, nada sai para a internet.

## 3. Instalação

Os comandos são executados na pasta do sistema (onde está este arquivo).

1. **Configuração.** Copie o modelo e troque cada `troque-isto` por um valor seu:
   ```bash
   cp .env.exemplo .env
   ```
   Para gerar valores aleatórios: `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`.
   O arquivo `.env` guarda senhas: não o envie a ninguém nem o coloque no controle de versão.

2. **Volume do modelo de IA** (guarda o modelo fora dos contêineres, para não baixá-lo de novo):
   ```bash
   docker volume create ia-teste_ollama-modelos
   ```

3. **Subir os serviços** (a primeira vez constrói as imagens):
   ```bash
   docker compose up -d --build
   ```

4. **Baixar o modelo de IA** (uma vez só, 3,3 GB). A IA fica numa rede sem internet (RNF11); para baixar, ela é
   ligada à rede comum só durante o download e desligada em seguida:
   ```bash
   docker network connect les-condeza_default les-condeza-ollama-1
   docker compose exec ollama ollama pull qwen3.5:4b-q4_K_M
   docker network disconnect les-condeza_default les-condeza-ollama-1
   ```

5. **Criar as tabelas e as contas de teste da equipe** (o `worker` espera as tabelas existirem e começa sozinho):
   ```bash
   docker compose exec app python -m sistema.banco
   ```

6. **Proteger a trilha de auditoria** (feito pelo administrador do banco, não pela aplicação, RNF19):
   ```bash
   docker compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" condeza' < banco/gatilhos.sql
   ```
   O script cria os gatilhos que recusam alterar ou apagar a trilha e retira do usuário da aplicação os privilégios de
   criar gatilhos, apagar tabelas e alterar a estrutura.

7. **Conferir:** http://127.0.0.1:5080/saude mostra banco, ERP, aplicativo e IA como `ok`.

## 4. Uso

| Quem | Onde | Como entrar |
|---|---|---|
| Visitante | http://127.0.0.1:5080 | Site da Condeza; o pedido de titularidade não exige cadastro |
| Condômino | http://127.0.0.1:5080/condomino/entrar | CPF de um condômino fictício (`simulados/aplicativo/inicial.json`) e a senha `CONDOMINO_TESTE_SENHA` do `.env` |
| Cadastro e Emissão | http://127.0.0.1:5080/equipe/entrar | `EQUIPE_TESTE_EMAIL` e `EQUIPE_TESTE_SENHA` do `.env` e o código do autenticador |
| Gerente da carteira | http://127.0.0.1:5080/equipe/entrar | `GERENTE_TESTE_EMAIL` e `GERENTE_TESTE_SENHA` do `.env` e o código do autenticador |
| Tecnologia (contas da equipe) | http://127.0.0.1:5080/equipe/entrar | `TECNOLOGIA_TESTE_EMAIL` e `TECNOLOGIA_TESTE_SENHA` do `.env` e o código do autenticador |
| E-mails enviados | http://127.0.0.1:8036 | Caixa local (Mailpit) |

**Segundo fator da equipe.** A carga inicial grava, no volume de documentos, o endereço para cadastrar cada conta num
aplicativo autenticador (`/dados/documentos/.totp_conferente_teste.txt`, `.totp_gerente_teste.txt` e `.totp_tecnologia_teste.txt`). Só para
desenvolvimento e demonstração, o código atual pode ser obtido sem autenticador:
```bash
docker compose exec app python -m sistema.codigo_2fa            # conferente
docker compose exec app python -m sistema.codigo_2fa gerente    # gerente da carteira 1
docker compose exec app python -m sistema.codigo_2fa tecnologia # Tecnologia
```

**Contas da equipe (RF25).** A Tecnologia cria, desativa e reativa as contas em *Contas da equipe* e reenvia o link
de ativação. A pessoa recebe por e-mail um link de uso único (24 horas) e nele define a própria senha e cadastra o
autenticador; a Tecnologia não vê pedidos nem conhece senhas. A primeira conta da Tecnologia vem da carga inicial.

**IA local.** `IA_HABILITADA=0` no `.env` desliga a IA (RNF11): os pedidos seguem com conferência manual, sem índice.

**Rotinas periódicas.** O `worker` roda a cada 5 minutos: nova tentativa de gravação no ERP e no aplicativo,
lembrete mensal de matrícula, encerramento de pendência sem resposta, eliminação de rascunhos e de documentos
vencidos e leitura das alterações de contato do aplicativo. Para rodar na hora, com uma data simulada:
```bash
docker compose exec app python -m sistema.rotinas --hoje 2026-12-20
```

**Registros.** `docker compose logs -f app worker`.

## 5. Testes

Os testes de ponta a ponta foram executados no desenvolvimento e não fazem parte desta publicação.

## 6. Publicação num servidor (demonstração)

Para publicar num servidor virtual (Ubuntu 24.04, 4 vCPU e 16 GB, RNF15), com um nome de domínio apontando para ele.
Continua com dados fictícios e serviços simulados (RNF03): é a demonstração, não a operação real.

1. No servidor: instalar o Docker (com o Compose) e liberar no firewall só as portas 22, 80 e 443.
2. Copiar a pasta do sistema sem o `.env`.
3. Criar um `.env` novo a partir do `.env.exemplo`, com senhas novas, `DOMINIO` e `MAILPIT_USUARIO`/`MAILPIT_SENHA`.
4. Seguir os passos 2 a 6 da instalação, sempre com os dois arquivos do Compose:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
   ```
5. Conferir `https://DOMINIO/saude`. O sistema fica em `https://DOMINIO`, e a caixa de e-mail em `https://DOMINIO/caixa/`.

Diferenças em relação ao ambiente local: o app roda no gunicorn, sem modo de depuração; só o proxy (Caddy) tem
porta aberta, com HTTPS automático e cabeçalhos de segurança; o cookie da sessão só trafega por HTTPS; o IP real do
cliente vem do proxy (limite de tentativas, RF05); a caixa de e-mail exige usuário e senha.

## 7. Voltar ao estado inicial

Apaga todos os pedidos, a trilha, os documentos e os e-mails; o código e os dados fictícios de partida não mudam.
A trilha só pode ser apagada recriando o banco, pelo administrador (root).
```bash
docker compose stop app worker
docker compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "DROP DATABASE condeza; CREATE DATABASE condeza CHARACTER SET utf8mb4"'
docker compose run --rm --no-deps app sh -c 'find /dados/documentos -mindepth 1 -delete; python -m sistema.banco'
docker compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" condeza' < banco/gatilhos.sql
rm -f simulados/dados/erp.json simulados/dados/aplicativo.json
docker compose restart erp-simulado app-simulado
curl -s -X DELETE http://127.0.0.1:8036/api/v1/messages
docker compose start app worker
```

## 8. Estrutura das pastas

```
app/sistema/            código do sistema
  __init__.py           criação da aplicação, páginas de saúde e suporte
  config.py             configuração e parâmetros de negócio (prazos, limites, retenção)
  modelos.py            tabelas do banco (SQLAlchemy)
  banco.py              criação das tabelas e contas de teste
  gateways.py           interfaces com o ERP e o aplicativo (RNF05)
  prazos.py             dias úteis e feriados (RD11)
  indicador.py          indicador de prazo (RF23)
  worker.py             fila da IA (RF08) e chamada das rotinas
  rotinas.py            rotinas periódicas (RF15, RF17, RF19, RF24, RD12)
  ia/                   extração pela IA, comparação e índice de conformidade (RF08, RD09, RD10)
  servicos/             e-mail, documentos, trilha, gravação, conciliação e retenção
  rotas/                site, pedido de titularidade, contestação, área do condômino, atendimento assistido, equipe, contas
  templates/            telas (Jinja) e modelos de e-mail
  static/               estilo, logotipo, fontes (licença OFL) e ícone
banco/                  scripts do administrador do banco (gatilhos e permissões)
proxy/                  configuração do proxy HTTPS da publicação (Caddyfile)
simulados/              ERP e aplicativo simulados, com os dados fictícios de partida
```

## 9. Créditos de terceiros

Fontes Nunito e Great Vibes: SIL Open Font License (`app/sistema/static/fontes/OFL-*.txt`).
Bibliotecas Python: `app/requirements.txt`. Modelo de IA Qwen 3.5 4B, executado pelo Ollama.
