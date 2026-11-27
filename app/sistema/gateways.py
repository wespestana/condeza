import requests

from . import config
from .util import digitos


class ServicoIndisponivel(Exception):
    pass


class _Cliente:
    def __init__(self, url, chave, origem="sistema-titularidade"):
        self.url = url
        self.cab = {"X-API-Chave": chave, "X-Origem": origem}

    def _req(self, metodo, caminho, **kwargs):
        url_completa = self.url + caminho
        try:
            resposta = requests.request(
                metodo, url_completa, headers=self.cab, timeout=10, **kwargs
            )
        except requests.RequestException as erro:
            raise ServicoIndisponivel(f"{self.url}: {type(erro).__name__}") from erro
        if resposta.status_code == 404:
            return None
        if resposta.status_code >= 400:
            raise ServicoIndisponivel(f"{self.url}{caminho}: HTTP {resposta.status_code}")
        return resposta.json()


class ERP(_Cliente):
    def __init__(self):
        super().__init__(config.ERP_URL, config.ERP_API_CHAVE)

    def condominios(self):
        return self._req("GET", "/api/condominios")

    def localizar_unidade(self, condominio_id, numero, bloco=None):
        params = {"condominio_id": condominio_id, "numero": str(numero).strip()}
        if bloco:
            params["bloco"] = bloco.strip()
        achadas = self._req("GET", "/api/unidades", params=params) or []
        return achadas[0] if len(achadas) == 1 else None

    def unidade(self, unidade_id):
        return self._req("GET", f"/api/unidades/{unidade_id}")

    def proprietarios(self, unidade_id):
        dados_unidade = self.unidade(unidade_id) or {"responsaveis": []}
        return [
            responsavel
            for responsavel in dados_unidade["responsaveis"]
            if responsavel["papel"] == "proprietario"
        ]

    def gravar_responsaveis(self, unidade_id, responsaveis):
        return self._req("PUT", f"/api/unidades/{unidade_id}/responsaveis", json=responsaveis)

    def unidades_do_responsavel(self, cpf_cnpj):
        parametros = {"cpf_cnpj": cpf_cnpj}
        return self._req("GET", "/api/responsaveis/unidades", params=parametros) or []

    def emissao(self, condominio_id):
        return self._req("GET", f"/api/condominios/{condominio_id}/emissao")


class Aplicativo(_Cliente):
    def __init__(self):
        super().__init__(config.APP_URL, config.APP_API_CHAVE)

    def autenticar(self, cpf_cnpj, senha):
        credenciais = {"cpf_cnpj": cpf_cnpj, "senha": senha}
        resultado = self._req("POST", "/api/autenticar", json=credenciais) or {}
        if resultado.get("valido"):
            return resultado
        return None

    def condomino(self, cpf_cnpj):
        return self._req("GET", f"/api/condominos/{cpf_cnpj}")

    def alterar_contato(self, cpf_cnpj, dados):
        return self._req("PUT", f"/api/condominos/{cpf_cnpj}/contato", json=dados)

    def alteracoes_contato(self, desde_id=0):
        return self._req("GET", "/api/alteracoes-contato", params={"desde_id": desde_id})

    def gravar_responsaveis(self, unidade_id, responsaveis):
        return self._req("PUT", f"/api/unidades/{unidade_id}/responsaveis", json=responsaveis)


def mesmo_documento(primeiro, segundo):
    return digitos(primeiro) == digitos(segundo)
