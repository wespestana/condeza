import os

DATABASE_URL = os.environ["DATABASE_URL"]
SECRET_KEY = os.environ["SECRET_KEY"]
ERP_URL = os.environ["ERP_URL"]
ERP_API_CHAVE = os.environ["ERP_API_CHAVE"]
APP_URL = os.environ["APP_URL"]
APP_API_CHAVE = os.environ["APP_API_CHAVE"]
OLLAMA_URL = os.environ["OLLAMA_URL"]
OLLAMA_MODELO = os.environ.get("OLLAMA_MODELO", "qwen3.5:4b-q4_K_M")
IA_HABILITADA = os.environ.get("IA_HABILITADA", "1") == "1"
IA_TEMPO_LIMITE = int(os.environ.get("IA_TEMPO_LIMITE", "180"))
IA_TEMPO_CARGA = int(os.environ.get("IA_TEMPO_CARGA", "300"))
SMTP_HOST = os.environ.get("SMTP_HOST", "mailpit")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "1025"))
EMAIL_REMETENTE = os.environ.get("EMAIL_REMETENTE", "nao-responda@condeza.test")
EMAIL_TECNOLOGIA = os.environ.get("EMAIL_TECNOLOGIA", "tecnologia@condeza.test")
URL_PUBLICA = os.environ.get("URL_PUBLICA", "http://127.0.0.1:5080")
PASTA_DOCUMENTOS = os.environ.get("PASTA_DOCUMENTOS", "/dados/documentos")
PRODUCAO = os.environ.get("PRODUCAO", "0") == "1"

LINK_VALIDADE_HORAS = 2
ATIVACAO_VALIDADE_HORAS = 24
SENHA_EQUIPE_MINIMO = 12
RASCUNHO_DIAS = 30
PENDENCIA_SEM_RESPOSTA_DIAS = 30
PRAZO_PENDENCIAS_DIAS_UTEIS = 2
PRAZO_CONCLUSAO_DIAS_UTEIS = 5
CERTIDAO_VALIDADE_DIAS = 30
TOKEN_TENTATIVAS = 5
TOKEN_BLOQUEIO_MINUTOS = 15
AREA_PROPRIETARIO_HORAS = 1
