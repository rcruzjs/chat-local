-- Dados iniciais: modelos, coleções de teste e tipos de documento do OCR.

INSERT INTO llm_models (nome, papel, endpoint, local, contexto_max, params) VALUES
  ('chat-4b',      'chat',      'http://gateway:4000', true, 8192,  '{"temperature": 0.3}'),
  ('chat-8b',      'chat',      'http://gateway:4000', true, 8192,  '{"temperature": 0.3}'),
  ('chat-moe-30b', 'chat',      'http://gateway:4000', true, 16384, '{"temperature": 0.3}'),
  ('vision-3b',    'visao',     'http://gateway:4000', true, 8192,  '{"temperature": 0.0}'),
  ('embed-bge-m3', 'embedding', 'http://embed:8081',   true, 8192,  '{"dim": 1024}'),
  ('rerank-bge-v2-m3', 'reranker', 'http://rerank:8082', true, 8192, '{}');

INSERT INTO collections (nome, descricao, nivel_acesso, sensivel) VALUES
  ('pcsf-institucional', 'Estatuto, regimentos e atas do PCSF', 'departamental', true),
  ('acervo-pessoal',     'Acervo pessoal do administrador (acesso restrito)', 'local', true);

INSERT INTO ocr_doc_types (codigo, nome, esquema_json, regras) VALUES
  ('extrato_bancario', 'Extrato bancário',
   '{"type":"object","required":["banco","conta","periodo_inicio","periodo_fim","saldo_inicial","saldo_final","lancamentos"],
     "properties":{"banco":{"type":"string"},"conta":{"type":"string"},
       "periodo_inicio":{"type":"string","format":"date"},"periodo_fim":{"type":"string","format":"date"},
       "saldo_inicial":{"type":"number"},"saldo_final":{"type":"number"},
       "lancamentos":{"type":"array","items":{"type":"object","required":["data","descricao","valor"],
         "properties":{"data":{"type":"string","format":"date"},"descricao":{"type":"string"},"valor":{"type":"number"}}}}}}',
   '["saldo_inicial + soma(lancamentos.valor) = saldo_final", "datas dentro do periodo"]'),
  ('nota_fiscal', 'Nota fiscal (DANFE)',
   '{"type":"object","required":["emitente","cnpj_emitente","chave_acesso","itens","valor_total"],
     "properties":{"emitente":{"type":"string"},"cnpj_emitente":{"type":"string"},"chave_acesso":{"type":"string"},
       "data_emissao":{"type":"string","format":"date"},
       "itens":{"type":"array","items":{"type":"object","properties":{"descricao":{"type":"string"},"quantidade":{"type":"number"},"valor_total":{"type":"number"}}}},
       "impostos":{"type":"number"},"valor_total":{"type":"number"}}}',
   '["CNPJ com digito verificador valido", "chave de acesso com 44 digitos", "soma(itens.valor_total) = valor_total"]'),
  ('boleto', 'Boleto',
   '{"type":"object","required":["beneficiario","linha_digitavel","vencimento","valor"],
     "properties":{"beneficiario":{"type":"string"},"linha_digitavel":{"type":"string"},
       "vencimento":{"type":"string","format":"date"},"valor":{"type":"number"}}}',
   '["digitos verificadores da linha digitavel"]'),
  ('comprovante_pagamento', 'Comprovante de pagamento (PIX, TED)',
   '{"type":"object","required":["pagador","recebedor","data","valor"],
     "properties":{"pagador":{"type":"string"},"recebedor":{"type":"string"},"data":{"type":"string","format":"date"},
       "valor":{"type":"number"},"id_transacao":{"type":"string"}}}',
   '["campos obrigatorios presentes", "valor numerico valido"]'),
  ('generico', 'Genérico (atas, contratos, ofícios)',
   '{"type":"object","properties":{"markdown":{"type":"string"}}}',
   '["confianca media do OCR acima do limiar"]');
