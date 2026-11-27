ALTER TABLE pedido
  ADD COLUMN aprovado_em DATETIME NULL,
  ADD COLUMN aprovado_por INT NULL,
  ADD COLUMN responsaveis_novos JSON NULL,
  ADD COLUMN gravado_erp_em DATETIME NULL,
  ADD COLUMN gravado_app_em DATETIME NULL,
  ADD COLUMN erro_gravacao TEXT NULL,
  ADD COLUMN concluido_em DATETIME NULL,
  ADD CONSTRAINT fk_pedido_aprovado_por FOREIGN KEY (aprovado_por) REFERENCES usuario(id);
