# SCP — Protótipo funcional v1.1

Aplicação web local para demonstrar o fluxo de contas a pagar definido na especificação do projeto. Esta versão acrescenta três melhorias de demonstração: prevenção de possíveis duplicidades, linha do tempo da despesa e painel preventivo de vencimentos.

## Funcionalidades
- Login com senha armazenada por hash.
- Perfis: administrador, solicitante, aprovador e financeiro.
- Cadastro com validação e ID sequencial único.
- **Prevenção de possíveis duplicidades:** compara documento do fornecedor e referência; também considera fornecedor, valor e vencimento. O alerta é um indício para conferência, não uma confirmação automática de fraude ou duplicidade.
- **Confirmação antes da aprovação:** quando há registros semelhantes, o aprovador precisa abrir os relacionados e marcar que conferiu antes de aprovar. A rejeição continua disponível sem essa confirmação.
- Visibilidade de registros conforme perfil e atribuição.
- Aprovação/rejeição com registro de decisão; rejeição exige justificativa.
- Bloqueio de pagamento sem aprovação.
- Confirmação manual com valor, data e referência.
- **Linha do tempo por despesa:** reúne cadastro, decisão, pagamento e cancelamento, com usuário e data/hora registrados.
- **Painel preventivo:** mostra contas vencidas, vencimentos nos próximos 7 dias e contas aprovadas ainda não pagas, respeitando a visibilidade de cada perfil.
- Cancelamento administrativo justificado, sem apagar o lançamento.
- Relatórios filtrados e exportação CSV.
- Auditoria e backup do banco SQLite.

## Execução
No Windows, execute `INICIAR.bat`. Acesse `http://127.0.0.1:5000`.

## Usuários de demonstração
- `admin` / `Admin@SCP2026!`
- `solicitante` / `Solicita@SCP2026!`
- `aprovador` / `Aprova@SCP2026!`
- `financeiro` / `Paga@SCP2026!`

Troque as senhas antes de qualquer uso que não seja demonstração.

## Atualização da v1.0 para v1.1 sem perder os registros
1. Feche a janela preta do SCP usando Ctrl+C.
2. Faça uma cópia de segurança do arquivo `scp.db` que fica dentro da pasta antiga `SCP_Sistema_v1`.
3. Extraia esta versão em uma pasta nova.
4. Se quiser manter os lançamentos e usuários anteriores, copie o `scp.db` da pasta antiga para a pasta nova `SCP_Sistema_v1`, substituindo apenas se tiver certeza de que fez a cópia de segurança.
5. Abra `INICIAR.bat` na pasta nova. A estrutura do banco desta atualização é compatível com a base da v1.0; não há migração de tabelas nesta versão.

Se preferir começar os testes do zero, não copie o banco antigo.

## Segurança e limites
Este é um protótipo local para avaliação acadêmica. Não foi submetido a teste de invasão, revisão independente ou implantação multiusuário. Não publicar na internet nem usar dados financeiros reais. A auditoria não é inviolável contra acesso direto ao arquivo SQLite ou à máquina. Para uso compartilhado, configurar segredo aleatório persistente, HTTPS, backups externos, política de acesso ao sistema operacional e revisão de segurança.

## Relação com a especificação
A v1.1 amplia a demonstração dos controles de duplicidade, rastreabilidade e vencimento. O relatório de validação continua pendente até a equipe executar e registrar os testes manuais. Os testes automatizados de apoio não substituem a validação manual exigida pela disciplina.

## Decisões provisórias adotadas
- O solicitante não aprova nem confirma pagamento.
- O aprovador é atribuído no cadastro e só decide contas atribuídas a ele.
- O financeiro só vê contas aprovadas/pagas e só confirma contas aprovadas.
- O administrador gerencia usuários, consulta tudo e cancela com justificativa; não aprova nem confirma pagamentos.
- Não há limites monetários ou múltiplas aprovações nesta versão.
Estas são premissas técnicas para o protótipo, não decisões oficiais da equipe.
