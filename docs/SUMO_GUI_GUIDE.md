# Guia Prático — Correr o Mobilis Line 9 no SUMO GUI (PowerShell)

Este guia reúne os comandos de **PowerShell** para lançar a simulação da **Linha 9 da Mobilis (Leiria)** em diferentes modos (só autocarro, com edifícios/mapa completo, com vários níveis de trânsito, outras horas do dia e modo *headless* rápido) e explica **porque é que cada ficheiro e opção importa** no projeto.

> **Premissa de todos os comandos abaixo:** o PowerShell está aberto na raiz do repositório (`MobilisTransit\`), por exemplo:
> ```powershell
> cd C:\Users\loupa\Desktop\PROJETO_4X\MobilisTransit
> ```

---

## 0. Verificação Rápida do Ambiente

Antes da primeira execução (num PowerShell **novo** aberto após instalar o SUMO), confirma que o SUMO está acessível:

```powershell
$env:SUMO_HOME
sumo-gui --version
```

- Não precisas de descompactar `leiria.net.xml.gz` — o SUMO lê ficheiros `.net.xml.gz` comprimidos diretamente.
- **Atalho sem PowerShell:** se só quiseres abrir rápido com trânsito e edifícios, podes fazer duplo clique em `sumo\run_gui_with_traffic.bat` (ou correr `.\sumo\run_gui_with_traffic.bat`).

---

## 1. Papel de Cada Ficheiro na Pasta `sumo/`

| Ficheiro | O que contém | Obrigatório na GUI? | Obrigatório no Backend/ML? |
| :--- | :--- | :--- | :--- |
| `sumo\leiria.net.xml.gz` | Rede rodoviária real de Leiria (OpenStreetMap via `osmWebWizard` + `netconvert`) | Sim | Sim |
| `sumo\pt_vtypes.xml` | Definição do tipo de veículo `bus` | Sim | Sim |
| `sumo\gtfs_pt_stops.add.xml` | Paragens oficiais da Mobilis mapeadas nas vias do SUMO (`gtfs_lra_*`) | Sim | Sim |
| `sumo\gtfs_pt_vehicles_L9.add.xml` | Percurso circular da Linha 9 (22 paragens, 10,2 km) e 12 viagens diárias (`07:10`–`19:45`) | Sim | Sim |
| `sumo\linha9.sumocfg` | Ficheiro de configuração base que agrupa os 4 ficheiros acima (`07:00`–`20:00`) | Opcional (atalho) | Sim (referência) |
| `sumo\generated_traffic\*.rou.xml` | Trânsito de carros de fundo gerado com `randomTrips.py` (ignorado pelo `.gitignore`) | Opcional | Sim (para variabilidade realista) |
| `sumo\deprecated\2026-11-21-19-40-29\osm.poly.xml.gz` | 7.388 polígonos (edifícios, parques, muralhas, rio) + 1.672 POIs | Opcional (visual) | **Não** (só gasta RAM em *headless*) |
| `sumo\deprecated\2026-11-21-19-40-29\osm.view.xml` | Definições visuais da GUI (`real world` + `delay = 20 ms`) | Opcional (visual) | **Não** |

---

## 2. Comandos por Modo de Execução

### Modo 1 — Linha 9 Simples (sem trânsito, sem edifícios)
O modo mais leve e rápido para validar rapidamente a rede, as paragens e o trajeto do autocarro.

```powershell
sumo-gui -c sumo\linha9.sumocfg
```

**Saltar logo para 5 segundos antes da 1.ª partida (`07:09:50` = `25790 s`):**
```powershell
sumo-gui -c sumo\linha9.sumocfg --begin 25785
```

> **Porque é útil o `--begin 25785`?** O `linha9.sumocfg` começa às `25200 s` (`07:00`), mas o primeiro autocarro só parte às `25790 s` (`07:09:50`). Sem `--begin`, os primeiros `590 s` mostram o mapa completamente vazio.

---

### Modo 2 — Linha 9 com Mapa Completo de Leiria (edifícios + `real world`, sem trânsito)
Excelente para te orientares geograficamente em Leiria (Estádio, Sé, Campus 2 IPL, Castelo) e tirar capturas limpas da rota.

```powershell
sumo-gui `
  -n sumo\leiria.net.xml.gz `
  -a "sumo\pt_vtypes.xml,sumo\gtfs_pt_stops.add.xml,sumo\gtfs_pt_vehicles_L9.add.xml,sumo\deprecated\2026-11-21-19-40-29\osm.poly.xml.gz" `
  --begin 25785 --end 72000 `
  --gui-settings-file sumo\deprecated\2026-11-21-19-40-29\osm.view.xml
```

> **Nota técnica:** quando se passa `-a` na linha de comandos junto com `-c linha9.sumocfg`, o `-a` **substitui** o `<additional-files>` do `.sumocfg`. Por isso, sempre que adicionamos `osm.poly.xml.gz`, passamos `-n` e a lista completa em `-a`.

---

### Modo 3 — Gerar Trânsito de Fundo (`randomTrips.py`)
Antes de correr os Modos 4 e 5, gera um (ou mais) perfis de carros de fundo dentro de `sumo\generated_traffic\` (esta pasta está no `.gitignore`, por isso não suja o Git).

```powershell
New-Item -ItemType Directory -Path sumo\generated_traffic -Force | Out-Null
```

**A) Pouco trânsito / fora de ponta (`-p 1.5`, 1 carro novo a cada 1,5 s):**
```powershell
python "$env:SUMO_HOME\tools\randomTrips.py" `
  -n sumo\leiria.net.xml.gz `
  --vehicle-class passenger `
  -b 25700 -e 29000 `
  -p 1.5 --seed 11 --fringe-factor 5 `
  -o sumo\generated_traffic\demo_trips_light.trips.xml `
  -r sumo\generated_traffic\demo_traffic_light.rou.xml
```

**B) Trânsito médio (`-p 0.5`, 1 carro novo a cada 0,5 s):**
```powershell
python "$env:SUMO_HOME\tools\randomTrips.py" `
  -n sumo\leiria.net.xml.gz `
  --vehicle-class passenger `
  -b 25700 -e 29000 `
  -p 0.5 --seed 22 --fringe-factor 5 `
  -o sumo\generated_traffic\demo_trips_medium.trips.xml `
  -r sumo\generated_traffic\demo_traffic_medium.rou.xml
```

**C) Hora de ponta / congestionamento (`-p 0.25`, 1 carro novo a cada 0,25 s):**
```powershell
python "$env:SUMO_HOME\tools\randomTrips.py" `
  -n sumo\leiria.net.xml.gz `
  --vehicle-class passenger `
  -b 25700 -e 29000 `
  -p 0.25 --seed 33 --fringe-factor 5 `
  -o sumo\generated_traffic\demo_trips_heavy.trips.xml `
  -r sumo\generated_traffic\demo_traffic_heavy.rou.xml
```

> **O que faz cada flag do `randomTrips.py` e porque importa:**
> - `--vehicle-class passenger`: cria apenas automóveis ligeiros (evita gerar outros autocarros ou camiões aleatórios).
> - `-b 25700 -e 29000`: janela da 1.ª viagem com **90 s de warm-up** antes da partida do autocarro (`25790 s`), para que as rotundas e semáforos já tenham filas quando a Linha 9 sai do terminal.
> - `-p <valor>`: período de inserção de carros (quanto **menor**, **mais trânsito**).
> - `--seed <n>`: garante reprodutibilidade exata — se ambos correrem com a mesma `seed`, obtêm exatamente os mesmos carros (`BR-005`).
> - `--fringe-factor 5`: dá 5× mais peso às entradas/saídas da rede (vias principais de acesso a Leiria) em vez de encher apenas ruas residenciais sem saída.
> - `-r <ficheiro.rou.xml>`: invoca automaticamente o `duarouter` para validar e calcular trajetos completos (descartando pares origem-destino sem ligação no OSM).

---

### Modo 4 — Linha 9 + Trânsito de Fundo (leve, sem edifícios)
Ideal para observar filas nos cruzamentos/rotundas com máxima fluidez de FPS na GUI.

```powershell
sumo-gui `
  -c sumo\linha9.sumocfg `
  -r sumo\generated_traffic\demo_traffic_medium.rou.xml `
  --begin 25700 `
  --ignore-route-errors
```

*(Troca `demo_traffic_medium.rou.xml` por `demo_traffic_light.rou.xml` ou `demo_traffic_heavy.rou.xml` conforme o perfil que geraste.)*

> **Porque funciona `-c` + `-r` aqui?** O `linha9.sumocfg` não define `<route-files>`, logo `-r` soma o trânsito de carros sem mexer nos ficheiros da Linha 9. O `--ignore-route-errors` garante que eventuais viagens aleatórias isoladas do OSM não travam a GUI.

---

### Modo 5 — Linha 9 + Trânsito + Edifícios + `real world` (Modo Demonstração Completo)
O comando mais completo para gravações, demonstrações em aula e inspeção visual detalhada (é exatamente o que o `sumo\run_gui_with_traffic.bat` executa):

```powershell
sumo-gui `
  -n sumo\leiria.net.xml.gz `
  -a "sumo\pt_vtypes.xml,sumo\gtfs_pt_stops.add.xml,sumo\gtfs_pt_vehicles_L9.add.xml,sumo\deprecated\2026-11-21-19-40-29\osm.poly.xml.gz" `
  -r sumo\generated_traffic\demo_traffic_medium.rou.xml `
  --begin 25700 --end 72000 `
  --ignore-route-errors `
  --gui-settings-file sumo\deprecated\2026-11-21-19-40-29\osm.view.xml
```

---

### Modo 6 — Testar Outras Viagens / Horas do Dia
O ficheiro `sumo\gtfs_pt_vehicles_L9.add.xml` tem **12 viagens** ao longo do dia útil:

| # | ID do Veículo | Hora | `depart` (s) | `--begin` sugerido (com 90 s de warm-up) |
| :-: | :--- | :-: | :-: | :-: |
| 1 | `1010-1-001-...-07h10-...` | 07:10 | `25790` | `25700` |
| 2 | `1010-1-002-...-07h50-...` | 07:50 | `28190` | `28100` |
| 3 | `1010-1-003-...-08h30-...` | 08:30 | `30590` | `30500` |
| 4 | `1010-1-004-...-09h20-...` | 09:20 | `33590` | `33500` |
| 5 | `1010-1-005-...-12h00-...` | 12:00 | `43190` | `43100` |
| 6 | `1010-1-006-...-12h40-...` | 12:40 | `45590` | `45500` |
| 7 | `1010-1-007-...-13h20-...` | 13:20 | `47990` | `47900` |
| 8 | `1010-1-008-...-17h00-...` | 17:00 | `61190` | `61100` |
| 9 | `1010-1-009-...-17h40-...` | 17:40 | `63590` | `63500` |
| 10 | `1010-1-010-...-18h20-...` | 18:20 | `65990` | `65900` |
| 11 | `1010-1-011-...-19h00-...` | 19:00 | `68390` | `68300` |
| 12 | `1010-1-012-...-19h45-...` | 19:45 | `71090` | `71000` |

**Exemplo — ver a viagem das 17:00 (`61190 s`) sem trânsito:**
```powershell
sumo-gui -c sumo\linha9.sumocfg --begin 61180 --end 64000
```
*(Se quiseres trânsito às 17:00, basta gerar o `.rou.xml` no Modo 3 com `-b 61100 -e 64000`.)*

---

### Modo 7 — Execução *Headless* Rápida com Estatísticas (`sumo` sem GUI)
Usado para testes automáticos, backend e geração de datasets de ML (corre > 30.000× mais rápido que tempo real):

```powershell
sumo -c sumo\linha9.sumocfg --begin 25780 --end 29000 --no-step-log --duration-log.statistics
```

---

## 3. Porque Cada Elemento é Importante no MobilisTransit (e Armadilhas a Evitar)

1. **Porque precisamos de trânsito de fundo (`randomTrips.py`)?**
   - Sem carros na rede, o autocarro circula sempre à velocidade livre do OSM e chega cedo a todas as paragens — não existem atrasos estocásticos nem filas nas rotundas de Leiria.
   - Os perfis de trânsito (`off_peak`, `campus_rush`, `heavy_congestion`) são indispensáveis para gerar variabilidade realista para os modelos de **AAP (Ridge / XGBoost)** e testar multiplicadores de tráfego (`DEC-002`).

2. **Porque NÃO usar `--scale` (nem `Scale Traffic` na GUI) em experiências científicas?**
   - A opção global `--scale` do SUMO (e o campo `Scale Traffic` na barra da GUI) escala **todos** os veículos, incluindo os autocarros da Linha 9 — o que pode **duplicar** (`scale > 1`) ou **fazer desaparecer** (`scale < 1`) o autocarro!
   - Na GUI podes mexer em `Scale Traffic` apenas por curiosidade visual, mas no nosso backend (`T002`) o `traffic_multiplier` escala **exclusivamente** o ficheiro de trânsito de fundo.

3. **Porque os edifícios (`osm.poly.xml.gz`) e o `osm.view.xml` só devem entrar na GUI?**
   - Os `7.388` polígonos tornam Leiria imediatamente reconhecível em apresentações e vídeos (`DEL-006`), mas não alteram a física do trânsito.
   - Nas corridas automáticas de backend/ML (`sumo` *headless*), não carregamos polígonos para manter o arranque rápido e o consumo de RAM baixo (`CON-010`).

4. **Porque o `--begin` deve começar antes da partida do autocarro (warm-up)?**
   - O SUMO ignora viagens com `depart < --begin`. Se começares exatamente às `25790 s`, as estradas arrancam vazias.
   - Começar `90 s` a `300 s` antes (`--begin 25700`) enche a rede de carros primeiro, tornando o trânsito nas primeiras paragens realista desde o arranque.

5. **Comportamento do `until="..."` no GTFS (nota importante para `T002` / `REQ-001`):**
   - O `gtfs2pt.py` colocou `until="..."` em todas as 22 paragens de `gtfs_pt_vehicles_L9.add.xml`. Em estrada vazia, o autocarro fica parado até `2–4 min` nalgumas paragens à espera da hora oficial.
   - Na GUI isto é normal com o ficheiro base, mas no nosso `traci_runner.py` (`T002`) vamos preservar os valores `until` como **horário de referência** (`reference_offset_s` para calcular atrasos em `BR-001`) enquanto simulamos paragens intermédias com tempos de embarque (`duration`) realistas + calibração de campo (`REQ-001`), garantindo que os atrasos se propagam corretamente ao longo da linha.

6. **Se um dia apagarem `sumo\deprecated\`:**
   - `osm.poly.xml.gz` e `osm.view.xml` estão atualmente dentro de `sumo\deprecated\2026-11-21-19-40-29\`. Se decidirem remover `deprecated\`, movam primeiro esses 2 ficheiros para `sumo\` (e atualizem o caminho nos comandos).

---

## 4. Dicas Rápidas Dentro da Janela do `sumo-gui`

- **Seguir o autocarro automaticamente:** menu **Locate → Locate Vehicles** (lupa com carro) → escolher `1010-1-001-...` → **Center** → botão direito sobre o autocarro → **Start Tracking**.
- **Ver os nomes das paragens no mapa:** ícone do arco-íris (**View Settings**, `F9`) → separador **Additional** → em *Bus stops*, ativar **Show name**.
- **Controlar a velocidade:** ajustar o slider **Delay (ms)** (`20–50 ms` é bom para seguir o autocarro; `0 ms` avança o mais depressa possível) ou usar o botão **Step** para avançar segundo a segundo.
- **Ver o trajeto completo do autocarro:** botão direito no autocarro → **Show Current Route**.
