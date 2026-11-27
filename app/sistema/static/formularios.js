document.addEventListener("submit", function (e) {
  var f = e.target;
  if (f.dataset.enviando) { e.preventDefault(); return; }
  f.dataset.enviando = "1";
  f.setAttribute("aria-busy", "true");
});
window.addEventListener("pageshow", function () {
  document.querySelectorAll("form[data-enviando]").forEach(function (f) {
    delete f.dataset.enviando;
    f.removeAttribute("aria-busy");
  });
});
function copiarAntigo(texto) {
  var campo = document.createElement("textarea");
  campo.value = texto;
  campo.setAttribute("readonly", "");
  campo.style.position = "fixed";
  campo.style.opacity = "0";
  document.body.appendChild(campo);
  campo.select();
  var ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  document.body.removeChild(campo);
  return ok;
}
document.querySelectorAll("[data-copiar]").forEach(function (b) {
  b.hidden = false;
  b.addEventListener("click", function () {
    var aviso = b.parentNode.querySelector("[data-copiado]");
    var texto = b.dataset.copiar;
    function resultado(ok) {
      aviso.textContent = ok ? "Chave copiada. Cole no aplicativo autenticador."
                             : "Não foi possível copiar. Selecione a chave acima ou digite-a.";
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(texto).then(function () { resultado(true); },
                                                function () { resultado(copiarAntigo(texto)); });
    } else {
      resultado(copiarAntigo(texto));
    }
  });
});

function semAcento(t) { return t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase(); }
document.querySelectorAll("select[data-busca]").forEach(function (lista) {
  var todas = Array.prototype.slice.call(lista.options);
  var bloco = document.createElement("div");
  bloco.className = "campo-busca";
  bloco.innerHTML = '<label for="' + lista.id + '-busca">Buscar condomínio pelo nome</label>' +
    '<input type="search" id="' + lista.id + '-busca" autocomplete="off" aria-describedby="' + lista.id + '-achados">' +
    '<p class="ajuda" id="' + lista.id + '-achados" role="status"></p>';
  var rotulo = lista.parentNode.querySelector('label[for="' + lista.id + '"]');
  lista.parentNode.insertBefore(bloco, rotulo);
  var campo = bloco.querySelector("input"), achados = bloco.querySelector("[role=status]");
  campo.addEventListener("input", function () {
    var termo = semAcento(campo.value.trim());
    var escolhido = lista.value;
    var ficam = todas.filter(function (o) { return o.value === "" || !termo || semAcento(o.text).indexOf(termo) >= 0; });
    lista.replaceChildren.apply(lista, ficam);
    var nomes = ficam.filter(function (o) { return o.value !== ""; });
    if (nomes.length === 1) lista.value = nomes[0].value;
    else lista.value = nomes.some(function (o) { return o.value === escolhido; }) ? escolhido : "";
    achados.textContent = !termo ? "" : nomes.length === 0 ? "Nenhum condomínio com esse nome."
      : nomes.length === 1 ? "1 condomínio encontrado e escolhido na lista abaixo."
      : nomes.length + " condomínios encontrados. Escolha na lista abaixo.";
  });
});

document.querySelectorAll("[data-alternar-contraste]").forEach(function (b) {
  var raiz = document.documentElement;
  b.hidden = false;
  b.setAttribute("aria-pressed", raiz.dataset.contraste === "alto" ? "true" : "false");
  b.addEventListener("click", function () {
    var ligar = raiz.dataset.contraste !== "alto";
    if (ligar) raiz.dataset.contraste = "alto"; else delete raiz.dataset.contraste;
    b.setAttribute("aria-pressed", ligar ? "true" : "false");
    try { localStorage.setItem("contraste", ligar ? "alto" : "normal"); } catch (e) {}
  });
});
