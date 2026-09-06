# AVA — Presentation Template

**Jezik prezentacije:** srpski
**Proizvod:** AVA — Autonomous Vehicle Analyst
**Tema:** proverljiv RAG asistent nad SEC 10-K filing-ima
**Predloženo trajanje:** 20–30 minuta
**Status:** template; tekst, vizuali i metrike su pripremljeni iz repository artefakata

Ovaj dokument je spreman za prebacivanje u PowerPoint, Google Slides, Keynote ili
Marp. Svaki slajd je označen brojem i eksplicitno navodi sadržaj, tekst, vizuale,
tabele, govorne beleške i izvore. Brojevi se ne smeju menjati bez provere
odgovarajućeg artifact-a.

## Slajd 1 — Naslov

**Tip:** naslovni slajd
**Naslov:** AVA — Autonomous Vehicle Analyst
**Podnaslov:** Grounded RAG asistent za SEC filing analizu
**Autor:** `[ime autora]`
**Datum:** `[datum prezentacije]`

**Vizuelni sadržaj:**

- `banner/banner_ava.png` ili AVA avatar iz `src/frontend/avatar/ava.png`;
- diskretna pozadina sa tamno-plavom temom interfejsa;
- bez dodatne biografije ili mascot narativa.

**Govorna napomena:** AVA nije opšti chatbot niti otvoreni autonomni agent. To
je ograničen analitički sistem koji povezuje SEC dokument, retrieval, proveru
dokaza, generation i korisnički prikaz izvora.

## Slajd 2 — Problem i motivacija

**Naslov:** Zašto je potrebna AVA?

**Tekst na slajdu:**

- SEC 10-K dokumenti su dugi, heterogeni i puni tabela, perioda, jedinica i
  različite terminologije.
- Kompanije iz autonomne vožnje opisuju slične teme različitim jezikom.
- Fluentan odgovor nije dovoljan ako se ne može proveriti u izvoru.
- Potreban je sistem koji razlikuje retrieval, generation, alate, memoriju i UI.

**Vizuelni sadržaj:**

- `screenshots/home_page.png` kao primer početnog AVA interfejsa;
- kratka grafička sekvenca: pitanje → dokaz → odgovor → izvor.

**Govorna napomena:** Motivacija nije tvrdnja da ovaj korpus meri celu industriju.
Korpus je ograničen da bi poređenje i evaluacija bili reproducibilni.

## Slajd 3 — Ciljevi i granice sistema

**Naslov:** Šta AVA treba da uradi — i šta ne treba

| Oblast | Cilj | Granica |
|---|---|---|
| SEC corpus | reproducibilni normalni 10-K snapshot-i | ne koristi 10-K/A kao primary filing |
| Retrieval | pronaći relevantan filing dokaz | Recall nije isto što i tačnost odgovora |
| Generation | grounded odgovor sa citation-ima | model nije evidence |
| Tools | current web i deterministička aritmetika | nema proizvoljnog koda ili akcija |
| Memory | korisnički preference/context | memory ne potvrđuje company facts |
| UI | prikaz odgovora i source card-ova | browser nije trust boundary |

**Govorna napomena:** Ove granice su važne jer omogućavaju merenje najranijeg
mesta na kom je dokaz izgubljen.

## Slajd 4 — Korpus

**Naslov:** Fiksirani korpus od 11 kompanija

| Ticker | Kompanija | Filing period | Chunk-ovi |
|---|---|---:|---:|
| APTV | Aptiv | 2025-12-31 | 528 |
| AUR | Aurora | 2025-12-31 | 261 |
| F | Ford | 2025-12-31 | 576 |
| GM | General Motors | 2025-12-31 | 410 |
| GOOGL | Alphabet | 2025-12-31 | 512 |
| MBLY | Mobileye | 2025-12-27 | 462 |
| NVDA | NVIDIA | 2026-01-25 | 329 |
| OUST | Ouster | 2025-12-31 | 375 |
| QCOM | Qualcomm | 2025-09-28 | 321 |
| RIVN | Rivian | 2025-12-31 | 411 |
| TSLA | Tesla | 2025-12-31 | 341 |

**Ključne ukupne vrednosti:** 12.602 strukturirana bloka, 978 logical tables,
3.561 narativni chunk-a, 965 tabelarnih chunk-ova i 4.526 ukupnih chunk-ova.

**Izvor:** `data/evaluation/finalization/v19/freeze_manifest.json` i
`INTERNSHIP_REPORT.md`.

## Slajd 5 — Acquisition i reproducibility

**Naslov:** Od SEC EDGAR API-ja do immutable snapshot-a

**Tekst:**

1. CIK/company registry identifikuje kompaniju.
2. SEC submissions endpoint pronalazi poslednji normalni `10-K`.
3. Primarni SEC HTML se preuzima uz deskriptivan `SEC_USER_AGENT`.
4. Metadata čuva datum, period, accession number i source URL.
5. Raw HTML se ne prepisuje bez eksplicitnog overwrite-a.

**Putanje:**

```text
data/raw/TICKER/YEAR-10-K.html
data/raw/TICKER/YEAR-10-K.metadata.json
```

**Kod:** `src/filings/fetch_data.py`, `src/filings/filing_io.py`,
`src/filings/corpus.py`.

**Govorna napomena:** Immutable input je preduslov za pošteno poređenje parsera,
chunkera, embeddinga i retrieval politika.

## Slajd 6 — Ingestion pipeline

**Naslov:** Struktura se čuva pre chunkovanja

**Dijagram:** `diagrams/sec_pipeline_sr.png`.

**Ključna poruka:** prvo se čuvaju section, paragraph, list, table, source tag i
metadata; tek onda se formiraju retrieval units.

## Slajd 7 — HTML čišćenje

**Naslov:** Čišćenje DOM-a bez gubitka vidljivog sadržaja

**Tabela:**

| Uklanja se | Čuva se |
|---|---|
| `script`, `style`, `noscript` | vidljivi Inline XBRL tekst |
| `ix:hidden` | headings i styled headings |
| slike/SVG i viewer content | paragraph i list granice |
| bezbedno identifikovan page furniture | document order |
| suvišni HTML whitespace | filing metadata i source anchors |

**Kod:** `src/filings/dom_processing.py`, `src/filings/block_extraction.py`.

**Govorna napomena:** Globalno dedupliranje nije primenjeno jer ponovljeni
heading ili table header može biti potreban za tumačenje nastavka dokumenta.

**Izvor:** `CLEANING_AND_cHUNKING_REPORT_FINAL.md`.

## Slajd 8 — Problem SEC tabela

**Naslov:** Zašto su tabele bile najteži ingestion problem?

**Tekst:**

- Header može biti bold `td`, a ne `th`.
- `rowspan`/`colspan` menjaju fizičku geometriju.
- Prazne alignment ćelije ne smeju pomeriti vrednosti.
- Početni balance red može izgledati kao header.
- Godine mogu biti pogrešno protumačene kao nazivi kolona.
- Jedna tabela može imati i novac i procente.
- Jedna logička tabela može biti razbijena na više HTML tabela.

**Vizuelni sadržaj:** `screenshots/retrieval_sources.png`, uz crop tabele po
potrebi.

## Slajd 9 — Logical-table schema

**Naslov:** Fizički HTML dokaz i logička tabela nisu isto

**Dijagram na slajdu:**

```text
HTML table
  → raw cells + coordinates + spans + XPath
  → span-aware rectangular grid
  → header/data boundary
  → title + section + units + row roles
  → logical table + raw-cell mapping
```

**Tabela:**

| Reprezentacija | Svrha |
|---|---|
| Physical evidence | audit prema originalnom DOM-u |
| Logical headers | čitljivo i precizno tumačenje |
| Logical rows | retrieval i frontend table rendering |
| Column units | sprečavanje pogrešne globalne jedinice |
| Raw-cell mapping | proverljivost i fallback |

**Metrike:** 1.005 fizičkih fragmenata, 978 logical tables, 965 indeksiranih
tabelarnih chunk-ova i 13 navigation tabela izuzetih iz retrieval-a.

## Slajd 10 — Chunking eksperiment

**Naslov:** Recursive token chunking kao promovisana strategija

| Strategija | Veličina/overlap | MBLY boundary accuracy | TSLA boundary accuracy |
|---|---:|---:|---:|
| Recursive | 250/32 | 76,4% | 77,8% |
| Fixed | 250/32 | 39,2% | 45,3% |
| Recursive | 500/64 | 92,1% | 89,1% |
| Fixed | 500/64 | 61,7% | 66,9% |

**Promovisana konfiguracija:** `RecursiveCharacterTextSplitter`, 500 narativnih
tokena, 32 konfigurisanog overlap-a, separator priority paragraph → line →
space → character, jedna kompletna logical table po tabelarnom chunk-u.

**Govorna napomena:** Actual overlap nije garantovan. Separator-aware recursive
splitter često završava na prirodnoj granici pre nego što je overlap potreban.

## Slajd 11 — Kompletne tabele i chunk metadata

**Naslov:** Zašto tabele nisu sečene na row fragments?

**Leva strana — problem:** fragmenti smanjuju veličinu, ali gube globalni odnos
između total-a, redova, kolona, units i header-a.

**Desna strana — odluka:** jedna zadržana logical table = jedan kompletan table
chunk, sa `table_headers`, `table_rows`, `column_units`, row indexes i source
block ID-em.

**Obavezni metadata:** company, ticker, CIK, filing year/date, reporting period,
accession, section, content type, source URL, block/source spans i schema version.

**Ograničenje:** kompletna tabela može biti veća od narativnog limita i embedding
model može skratiti searchable input, dok source JSON ostaje kompletan.

## Slajd 12 — Embeddings

**Naslov:** Izbor BGE-base v1.5

| Podešavanje | Vrednost |
|---|---|
| Model | `BAAI/bge-base-en-v1.5` |
| Revision | `a5beb1e3e68b9ab74eb54cfd186867f64f240e1a` |
| Dimenzija | 768 |
| Normalizacija | da |
| Query prefix | `Represent this sentence for searching relevant passages: ` |
| Document prefix | prazan |
| Manifest | manifest-v3 |

**Rezultat:** 4.526 aligned vectors za 4.526 promoted chunk-ova.

**Metrike:** MiniLM dense Recall@10 `0,5472`, MRR@10 `0,3171`; BGE dense
Recall@10 `0,7072`, MRR@10 `0,5168` na saved 300-question benchmarku.

**Ograničenje:** current manifests beleže 322 table embedding inputs iznad
512-token modelskog limita; narrative inputs nisu skraćeni.

## Slajd 13 — Retrieval rezultati

**Naslov:** Dense, BM25 i hybrid retrieval

| Retrieval path | N | Recall@10 | Hit@10 | MRR@10 | Status |
|---|---:|---:|---:|---:|---|
| MiniLM dense | 300 | 0,5472 | 0,6300 | 0,3171 | historical baseline |
| BGE dense | 300 | 0,7072 | 0,7967 | 0,5168 | selected dense baseline |
| BM25 | 300 | 0,7517 | 0,8400 | 0,5428 | lexical comparison |
| BGE/BM25 RRF | 300 | 0,8117 | — | 0,6157 | historical hybrid |
| Scope-aware hybrid | 300 | 0,8411 | — | 0,6157 | historical promoted comparison |

**Govorna napomena:** Hybrid vrednosti su historical corpus comparison; ne treba
ih mešati sa 75-case end-to-end evaluacijom.

## Slajd 14 — Scope-aware retrieval

**Naslov:** Candidate recall nije final evidence recall

**Tok:**

```text
query
  → company/ticker/alias detection
  → atomic subqueries
  → independent company pools
  → dense + BM25 + RRF
  → stable-ID merge/dedup
  → minimum 2 chunks per subquery
  → balanced quota allocation
  → final 10-chunk context
```

**Ključna P0 lekcija:** candidate recall je bio `1,0`, ali final recall `0,5887`.
Problem je bio selector, ne kandidat retrieval.

**Promovisana mala P0 evaluacija:** candidate recall `1,0`, final recall `1,0`,
quota satisfaction `1,0`, source-display exactness `1,0`.

## Slajd 15 — Qdrant indeks

**Naslov:** Od lokalnog NPZ oracle-a do persistent vector storage-a

**Tekst:**

- Qdrant server `1.18.2`, client `1.19.0`.
- Alias: `ava_filing_chunks_current`.
- Physical collection: `ava_filing_chunks_89d3a5be9e7d7a8e`.
- 4.526 points, 768 dimensions, Dot distance.
- Deterministički point IDs i payload filters.
- Import, point-count, exact-search, snapshot i parity audit su prošli.

**Dijagram:** Qdrant primary ↔ local NPZ/BM25 shadow/parity oracle.

**Govorna napomena:** Qdrant failure u primary režimu obara readiness; sistem ne
skriva kvar mock odgovorom.

## Slajd 16 — Grounded generation i citations

**Naslov:** Model generiše odgovor, ali ne određuje dokaz

**Tri koraka:**

1. Final evidence se formatira sa source ID-evima i filing metadata.
2. Prompt zahteva factual claim + neposredni citation.
3. Server prihvata samo citation ID koji pripada finalnom generation context-u.

**Rezultat citation popravke:** historical P0 source-display exactness `0,4286`;
posle cited-only resolution `1,0` na frozen gate-u.

**Dijagram:** `diagrams/evidence_validation_sr.png`.

## Slajd 17 — Bounded planner

**Naslov:** Agentic RAG bez otvorenog autonomnog agenta

**Dijagram:** `diagrams/bounded_planner_sr.png`.

**Plan može sadržati:** filing retrieval, upload retrieval, trusted web, direct/
evidence calculation, conversation ili clarification.

**Server validira:** task schema, canonical ticker, dependencies, memory
ownership, source keys, URL, limits i ambiguity.

**V18 rezultat:** 60/60 slučajeva, route accuracy `1,0`, web-required recall
`1,0`, unnecessary web calls `0`, calculator false positives `0`.

**Ograničenje:** planner može odbiti nejasan follow-up ili malformed provider
plan; bezbedno odbijanje nije dokaz da requested fact ne postoji.

## Slajd 18 — Web i calculator

**Naslov:** Alati sa različitim ugovorima

| Alat | Kada se koristi | Kontrola |
|---|---|---|
| Tavily web | current/news/market/explicit online | trusted domains, HTTPS, timestamps |
| Decimal calculator | genuine arithmetic | typed route, plain operands, `Decimal` |
| Filing retrieval | frozen disclosure | SEC corpus and citation |

**Web:** ticker-aware query rewriting, allowlisted source keys, SSRF/redirect
zaštita, quote timestamp i disclosed delay.

**Calculator:** ne izvršava kod; ne računa repetition, enumeration ili name/letter
manipulation. Historical Phase 10 regression: 10/10, exactness `1,0`.

**Vizuali:** `screenshots/web_search_source_cropped.png` i
`screenshots/ford_tesla_combined_revenue_calculation_cropped.png`.

## Slajd 19 — History, uploads i memory

**Naslov:** Personal context nije evidence

**Tabela:**

| Komponenta | Store | Uloga | Trust status |
|---|---|---|---|
| Transcript | PostgreSQL | owner-scoped ordered history | context |
| Short-term turns | PostgreSQL/context builder | bounded follow-up context | untrusted context |
| Long-term memory | PostgreSQL + separate Qdrant | opt-in preferences/reference | untrusted context |
| Uploads | owner/chat Qdrant + source storage | user-provided evidence | attributed evidence |
| SEC filing | filing Qdrant/NPZ | primary disclosure | evidence |

**History rezultat:** query-only planner accuracy `0,6667`, contextual accuracy
`0,8889`, history-dependent contextual accuracy `1,0`, history delta `1,0`.

**Vizuali:** `screenshots/memory_settings.png`,
`screenshots/preferred_company's_ceo_cropped.png`,
`screenshots/upload_showcase_fullscreen.png`.

## Slajd 20 — Frontend i UX

**Naslov:** Dokaz mora biti razumljiv i bezbedno prikazan

**Funkcije:**

- React + TypeScript + Vite;
- responsive desktop/mobile layout;
- light/dark teme;
- Settings: General, Memory, Personalization;
- company scope, recent/pinned chats i upload meni;
- Serbian/English UI;
- narrative i structured table source cards;
- SSE `delta`, `sources`, `done`, `error` contract;
- no raw internal IDs, prompts, scores, keys ili provider stack traces.

**Vizuali:** `screenshots/general_settings.png`,
`screenshots/personalization_settings.png`, `screenshots/serbian_answer_cropped.png`.

## Slajd 21 — Deployment i security

**Naslov:** Server-side ownership i readiness

```text
Vite frontend → FastAPI → PostgreSQL
                         ↘ Qdrant
                         ↘ SEC / Tavily / model provider
```

**Deployment činjenice:** `start_app.sh` pokreće lokalne servise, proverava
migracije/index, čeka readiness i pokreće frontend. Provider keys ostaju
backend-only. PostgreSQL/Qdrant nisu javno izloženi.

**Security kontrole:** OIDC/tenant-user ownership, Qdrant filters, CSRF, secure
cookies, rate limits, body/upload limits, timeout-i, bounded retries, safe
browser errors, Markdown bez raw HTML-a, prompt-injection quarantine i
access-controlled logs.

## Slajd 22 — Observability

**Naslov:** Od pitanja do prikazanog source card-a

**Tekst:** Request trace beleži original query, resolver, subqueries, candidates,
quota/token allocation, final evidence, answer, citations, source-display status,
stage latency, first-token latency, provider usage kada postoji, cancellation i
redigovan error class.

**Dijagram na slajdu:**

```text
query → plan → candidates → final evidence → prompt → answer
      → citations → displayed sources → latency/error trace
```

**Napomena:** Browser dobija samo opaque `X-Request-ID`; trace nije browser
analytics niti conversation history.

## Slajd 23 — Evaluaciona metodologija

**Naslov:** Retrieval, generation i tools mere se odvojeno

| Evaluacioni sloj | Veličina | Glavna pitanja |
|---|---:|---|
| Corpus retrieval | 300 pitanja | Recall, Hit, Complete, MRR, latency |
| QA/generation | 75 slučajeva | claim support, relevance, abstention, citations |
| Route/tool | 60 slučajeva | route, ticker, tool sequence, freshness |
| Conversation | 9 turn-ova + 4 state slučaja | follow-up, recall, isolation, deletion |
| Memory | 20 slučajeva | save, edit, delete, relevance, owner |
| Security | direct + boundary cases | injection, secret/ID leakage |
| Language | 10 paired prompts | route, recall, numbers, citation parity |
| Frontend | 43 Vitest + lint/type/build | UI and API contracts |

**Metodološko pravilo:** prvo se proverava da li je dokaz retrieval-ovan. Ako
nije, problem je retrieval/indexing; ako jeste, problem je generation/prompt/
grounding.

## Slajd 24 — Šta nije uspelo i šta je popravljeno

**Naslov:** Najvažnije failure analize

| Failure | Root cause | Fix |
|---|---|---|
| pogrešni table headers | bold `td`, spans, years | table-schema-v2 |
| candidate postoji, final ga gubi | imbalanced selector | balanced evidence allocation |
| gubitak kompanije u comparison-u | više nezavisnih router odluka | atomic bounded plan |
| current query ide u filing | stale web fallback | fail-closed web route |
| CEO/name manipulation ide u calculator | keyword false positive | genuine arithmetic classifier |
| citation panel prikazuje previše | fallback na final evidence | exact cited-only resolution |
| memory meša typed odnose | untyped semantic scope | typed memory references |
| provider plan odbijen zbog presentation drift-a | JSON fence/known shape | narrow normalization |
| model selection race | shared mutable generator model | request-scoped model |

**Govorna napomena:** Negativni eksperimenti su sačuvani. Strict-abstention prompt
je poboljšao pojedinačnu abstention metriku, ali pogoršao completeness,
numerical correctness, citation recall i latency, pa nije promovisan.

## Slajd 25 — Trade-off-i

**Naslov:** Zašto ove arhitektonske odluke?

| Odluka | Dobit | Cena |
|---|---|---|
| immutable SEC HTML | reproducibility | ručno osvežavanje snapshot-a |
| structured blocks | section/table provenance | složeniji parser |
| complete logical table | globalni table context | duži table chunks |
| BGE-base | bolji dense ranking | veći compute i vektori |
| BM25 + RRF | lexical/semantic complement | drugi index i fusion |
| Qdrant | persistence i filteri | service/parity operacije |
| bounded planner | mixed tasks bez arbitrary agent-a | ambiguity/failure handling |
| Decimal calculator | tačna aritmetika | ograničen task scope |
| server ownership | sigurniji tenant boundary | kompleksniji backend |

## Slajd 26 — Trenutne granice

**Naslov:** Šta rezultat ne tvrdi

- Ne tvrdi univerzalnu factual accuracy za sve nove upite.
- LLM judge je dijagnostički, a human review je ograničen jednim reviewer-om.
- Web zavisi od trusted provider rezultata, timestamp-a i konfiguracije.
- Gateway može isporučiti buffered odgovor umesto pravog token stream-a.
- Serbian citation-ID parity je `0,8`; resolution, route, gold recall i numerical
  parity su `1,0` u saved paired testu.
- Image ingestion/retrieval nije implementiran; Phase 6 je preskočen.
- Parser ne zaključuje tabele iz vizuelno poravnatih pasusa.

**Vizuelni sadržaj:** `screenshots/in_progress.png` može se koristiti samo kao
status UI; ne predstavljati ga kao chain-of-thought prikaz.

## Slajd 27 — Budući rad

**Naslov:** Sledeći merljivi koraci

**Autorizovani pravci:**

- bolji planner context resolution i typed memory references;
- mixed filing/web planovi i jači quote provider-i;
- Mobileye-scoped cross-encoder reranking experiment;
- bolji multilingual retrieval;
- veći QA skup i više nezavisnih human review-era;
- concurrency-safe model selection i dalja modularizacija;
- richer source editing i UI polish;
- production OIDC, retention, backup/restore i global rate limiting.

**Pravilo promocije:** novi kandidat mora imati definisan failure, development/
validation rezultat, before/after metrike i rollback/freeze artifact.

## Slajd 28 — Zaključak

**Naslov:** Šta AVA demonstrira

**Tekst na slajdu:**

- Reproducibilan SEC ingestion i immutable corpus.
- Struktura i kompletne tabele sačuvane pre retrieval-a.
- BGE + BM25 + RRF i scope-aware evidence selection.
- Grounded generation sa cited-only source prikazom.
- Bounded routing, trusted web, Decimal calculator, owner memory i uploads.
- Merenje po slojevima umesto jedne nejasne chatbot metrike.

**Završna poruka:** AVA nije sistem koji obećava da je svaki odgovor tačan.
AVA je sistem koji omogućava da se za odgovor proveri njegov input, plan,
retrieved evidence, alat, citation i prikazani source.

## Slajd 29 — Demo scenario

**Naslov:** Predloženi live demo

**Redosled:**

1. Prazan chat: `screenshots/home_page.png`.
2. Filing pitanje o Rivian CEO-u: `screenshots/rivian_ceo.png`.
3. Sources panel i structured evidence: `screenshots/filings_retrieval_sources_cropped.png`.
4. Current Tesla price: `screenshots/web_search_source_cropped.png`.
5. Mixed filing/web query: `screenshots/tesla_ceo_and_stock_price_cropped.png`.
6. Evidence-derived calculation: `screenshots/ford_tesla_combined_revenue_calculation_cropped.png`.
7. Serbian answer: `screenshots/serbian_answer_cropped.png`.
8. Memory settings i recall: `screenshots/memory_settings.png` i
   `screenshots/preferred_company's_ceo_cropped.png`.
9. Upload source flow: `screenshots/upload_showcase_fullscreen.png`.

**Demo upozorenje:** live web, calculator, memory i upload ponašanje zavise od
effective runtime konfiguracije. Ako provider nije dostupan, prikazati saved
artifact ili jasno označiti unavailable behavior; ne simulirati rezultat kao
live evidence.

## Slajd 30 — Reference i Q&A

**Naslov:** Reference

**Na slajdu navesti:**

- [README.md](README.md)
- [FINAL_REPORT.md](FINAL_REPORT.md)
- `data/evaluation/finalization/v19/freeze_manifest.json`
- `data/evaluation/finalization/v19/phase6/RESULTS.md`
- `data/evaluation/finalization/v19/phase7/final_summary.json`
- `data/evaluation/dense_retrieval/`
- `data/evaluation/bm25_retrieval/`
- `data/evaluation/ava_p0/`
- `src/filings/`, `src/retrieval/`, `src/generation/`, `src/orchestration/`,
  `src/conversations/`, `src/documents/`, `src/tools/`, `src/backend/` i
  `src/frontend/`.

**Završni element:** “Pitanja?” uz AVA avatar. Ne navoditi deprecated planove kao
aktivne izvore i ne predstavljati future work kao implementiranu funkciju.

## Vizuelni inventar

| Asset | Predložena upotreba |
|---|---|
| `screenshots/home_page.png` | početno stanje |
| `screenshots/greeting.png` | početak razgovora |
| `screenshots/in_progress.png` | reasoning/status UI |
| `screenshots/general_settings.png` | General settings |
| `screenshots/memory_settings.png` | Memory settings |
| `screenshots/personalization_settings.png` | personalization |
| `screenshots/sources_sidebar_fullscreen.png` | sidebar i Sources |
| `screenshots/filings_retrieval_sources_cropped.png` | filing sources |
| `screenshots/retrieval_sources.png` | retrieval evidence |
| `screenshots/rivian_ceo.png` | cited filing answer |
| `screenshots/preferred_company's_ceo_cropped.png` | memory-resolved reference |
| `screenshots/tesla_ceo_and_stock_price_cropped.png` | mixed filing/web |
| `screenshots/web_search_source_cropped.png` | web source |
| `screenshots/web_search_sources_fullscreen.png` | web sources |
| `screenshots/tesla_stock_price.png` | current web answer |
| `screenshots/ford_tesla_combined_revenue_calculation_cropped.png` | calculator |
| `screenshots/serbian_answer_cropped.png` | localization |
| `screenshots/upload_showcase_fullscreen.png` | upload menu |
| `screenshots/upload_raw_text_fullscreen.png` | pasted text upload |
| `diagrams/sec_pipeline_sr.png` | srpski ingestion/retrieval architecture |
| `diagrams/bounded_planner_sr.png` | srpski bounded task planner |
| `diagrams/evidence_validation_sr.png` | srpski evidence/citation flow |

## Presenter checklist

- [ ] Explain frozen v19 status and source commit.
- [ ] Distinguish historical baseline, candidate, promoted implementation and
      frozen release.
- [ ] Explain candidate recall versus final recall.
- [ ] Mention that LLM judge is diagnostic only.
- [ ] State Serbian citation-ID parity exception `0,8`.
- [ ] Do not claim image ingestion or arbitrary-agent behavior.
- [ ] Do not expose API keys, prompts, raw IDs or internal retrieval scores.
- [ ] Verify live provider configuration before demoing web/calculator/memory.
- [ ] Link questions to `FINAL_REPORT.md` and saved artifacts.
