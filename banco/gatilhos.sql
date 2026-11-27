DROP TRIGGER IF EXISTS evento_sem_update;
CREATE TRIGGER evento_sem_update BEFORE UPDATE ON evento FOR EACH ROW
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Trilha de auditoria: somente acrescimo (RNF19)';
DROP TRIGGER IF EXISTS evento_sem_delete;
CREATE TRIGGER evento_sem_delete BEFORE DELETE ON evento FOR EACH ROW
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Trilha de auditoria: somente acrescimo (RNF19)';
REVOKE TRIGGER ON condeza.* FROM 'condeza'@'%';
FLUSH PRIVILEGES;

REVOKE DROP, ALTER ON condeza.* FROM 'condeza'@'%';
