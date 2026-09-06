# AVA — završni tehnički izveštaj

**Proizvod:** AVA (Autonomous Vehicle Analyst)  
**Tip sistema:** RAG asistent nad SEC 10-K dokumentima  
**Frozen release candidate:** v19  
**Datum zamrzavanja:** 6. septembar 2026.  
**Izvorni commit:** `56d3ae603b7240d82f294e9471befd20fe3da306`  
**Glavni izvori:** `data/evaluation/finalization/v19/freeze_manifest.json`, `data/evaluation/finalization/v19/phase6/RESULTS.md`, `data/evaluation/finalization/v19/phase7/final_summary.json`

## 1. Uvod i motivacija

AVA je browser-based analitički asistent za pitanja o godišnjim izveštajima
javnih kompanija. Cilj nije samo generisati tečan odgovor, već omogućiti da se
svaka tvrdnja proveri u konkretnom SEC izvoru. Zbog toga su retrieval, generation,
računanje, web alati, memorija i korisnički interfejs tretirani kao odvojene
odgovornosti.

Osnovni tok je:

```text
SEC EDGAR → nepromenjeni HTML → strukturirani blokovi → chunk-ovi i vektori
→ dense/BM25/RRF retrieval → izbor dokaza → grounded generation → validirane citacije
→ bezbedno prikazivanje izvora u AVA interfejsu
```

Retrieval određuje da li je potreban dokaz pronađen. Generation taj dokaz
pretvara u odgovor, ali modelov izlaz nikada nije sam po sebi dokaz. Calculator
izvršava proverljivu aritmetiku; web rešava vremenski promenljive upite; memorija
čuva korisnički kontekst, ali ne potvrđuje činjenice o kompanijama. UI prikazuje
rezultat i izvore, ali ne određuje kompaniju, scope, plan ili citate.

## 2. SEC korpus

Korpus je namerno fiksiran na jedanaest kompanija iz autonomne vožnje i susednih
tehnoloških oblasti: Aptiv, Aurora, Ford, General Motors, Alphabet, Mobileye,
NVIDIA, Ouster, Qualcomm, Rivian i Tesla. Ograničenje omogućava kontrolisano
poređenje disclosure-a, reproducibilne indekse i evaluaciju sa poznatim
scope-om. Rivian je dodat nakon početnog desetokompanijskog milestone-a; ta
istorijska promena nije sakrivena.

| Ticker | Kompanija | Period | Datum podnošenja | Blokovi | Chunk-ovi | Narativni | Tabelarni |
|---|---|---:|---:|---:|---:|---:|---:|
| APTV | Aptiv PLC | 2025-12-31 | 2026-02-06 | 1.367 | 528 | 394 | 134 |
| AUR | Aurora Innovation | 2025-12-31 | 2026-02-11 | 909 | 261 | 227 | 34 |
| F | Ford Motor | 2025-12-31 | 2026-02-11 | 1.713 | 576 | 444 | 132 |
| GM | General Motors | 2025-12-31 | 2026-01-27 | 1.066 | 410 | 286 | 124 |
| GOOGL | Alphabet | 2025-12-31 | 2026-02-05 | 1.260 | 512 | 345 | 167 |
| MBLY | Mobileye | 2025-12-27 | 2026-02-12 | 1.258 | 462 | 412 | 50 |
| NVDA | NVIDIA | 2026-01-25 | 2026-02-25 | 980 | 329 | 273 | 56 |
| OUST | Ouster | 2025-12-31 | 2026-03-02 | 1.112 | 375 | 315 | 60 |
| QCOM | Qualcomm | 2025-09-28 | 2025-11-05 | 830 | 321 | 267 | 54 |
| RIVN | Rivian | 2025-12-31 | 2026-02-12 | 1.162 | 411 | 323 | 88 |
| TSLA | Tesla | 2025-12-31 | 2026-01-29 | 945 | 341 | 275 | 66 |
| **Ukupno** |  |  |  | **12.602** | **4.526** | **3.561** | **965** |

Vrednosti su iz `INTERNSHIP_REPORT.md` i odgovaraju v19 freeze manifestu.
Aktivni filing je poslednji regularni `10-K`; `10-K/A` nije primarni dokument.
Filing se preuzima sa SEC-hosted HTML-a, a metadata sadrži company, ticker, CIK,
form, filing date, reporting period, accession number i `source_url`. Aktivni
frozen filings su `2025-10-K`, osim NVIDIA `2026-10-K` prema
`data/evaluation/finalization/v19/freeze_manifest.json`.

## 3. Opseg kompanija i motivacija

Autonomna vožnja kombinuje automobilske proizvođače, lidar i sensing dobavljače,
čipove, softver i data/AI infrastrukturu. Disclosure-i zato koriste različite
termine, fiskalne periode, segmentacije i nivoe finansijske detaljnosti. AVA je
nastala iz potrebe da se te objave analiziraju konzistentno, uz interesovanje za
autonomna vozila i poređenje konkurentnih strategija.

Ovo je motivacija projekta, a ne tvrdnja da korpus meri stanje cele industrije.
Korpus je uzan da bi sistem mogao da bude proverljiv: kompanijski scope, filing
snapshot, retrieval dokaz i evaluacioni label ostaju kontrolisani.

## 4. Ingestion i acquisition

`src/filings/fetch_data.py` poziva SEC company-submissions endpoint, traži prvi
tačan normalni `10-K` i preuzima primarni HTML dokument iz SEC arhive. Zahteva se
deskriptivan `SEC_USER_AGENT`, a mrežni pozivi imaju timeout. Rezultat je:

```text
data/raw/TICKER/YEAR-10-K.html
data/raw/TICKER/YEAR-10-K.metadata.json
```

Raw HTML se ne menja i postojeći snapshot se ne prepisuje bez eksplicitnog
`overwrite`. `download-latest` i `process-existing` su odvojeni koraci. Kasniji
kod proverava da se ticker, CIK, godina, period i form slažu sa metadata zapisom.
Hash-evi raw metadata, processed blokova, chunk-ova, embedding manifest-a i
evaluation manifest-a su u v19 freeze manifestu. Time je moguće razlikovati
promenu parsera od promene ulaznog filing-a.

Relevantni moduli i provere su `src/filings/filing_io.py`,
`src/filings/fetch_data.py`, `src/filings/corpus.py`, `src/filings/release_state.py`,
`tests/test_corpus.py`, `tests/test_live_state.py` i freeze evaluacija.

## 5. HTML čišćenje i parsiranje

Autoritativni izvor ove faze je `CLEANING_AND_cHUNKING_REPORT_FINAL.md`.
Čišćenje ne koristi jedan globalni `get_text()`, jer bi se izgubile granice
pasusa, section pripadnost, redosled i relacije ćelija.

U `src/filings/dom_processing.py` uklanjaju se `script`, `style`, `noscript`,
slike/SVG, HTML head, eksplicitno skriveni elementi, `ix:hidden` i bezbedno
prepoznati viewer/navigation sadržaji. Vidljivi Inline XBRL wrapper-i se
uklanjaju, ali njihov vidljivi tekst ostaje. Normalizuju se HTML entiteti,
`\xa0`, soft hyphen, zero-width karakteri i višestruki razmaci. Globalno
dedupliranje nije uvedeno jer ponovljena rečenica može biti legitimna, a
ponovljeno zaglavlje tabele može biti potrebno u nastavku.

`src/filings/block_extraction.py` obilazi očišćeni DOM po redosledu pojavljivanja.
Pretraga stvarnog Item 1 izbegava cover-page i table-of-contents kopije. Naslovi
se prepoznaju i kada nisu `h1`/`h2`, na primer kroz kratke bold/styled pasuse.
Emit-once logika sprečava da tabela bude izdata kao tabela i zatim ponovljena kao
pojedinačni pasusi.

Svaki block ima deterministički `block_id`, ordinal, company/ticker/CIK, filing
metadata, `section`, `section_path`, `content_type`, source tag, anchor i SEC
URL. JSONL output se validira i upisuje atomski.

### Tabele i parsing eksperimenti

Tabela je bila glavni izvor grešaka. SEC HTML često koristi bold `td` umesto
`th`, prazne alignment kolone, odvojene `$` i brojeve, višeredna zaglavlja i
nastavke kroz više fizičkih tabela. Na MBLY pilotu su pronađeni pogrešno tretirani
headers (`Name | Age | Position`), početni balance redovi u header-u, godine
protumačene kao kolone, izgubljeni naslovi RSU/debt tabela i jedna pogrešna
globalna jedinica za tabelu sa novcem i procentima.

`src/filings/table_processing.py` zato prvo čuva fizičke DOM ćelije, koordinate,
`rowspan`/`colspan`, formatiranje, XPath i HTML fingerprint. Zatim gradi
odvojenu logical-table reprezentaciju sa semantic lanes, header paths, row roles,
title, region, continuation linkovima, per-column units i raw-cell mapiranjem.
Span-aware pravougaona mreža popunjava logičke pozicije; unutrašnje prazne ćelije
se čuvaju, dok se samo potpuno prazni redovi/kolone mogu ukloniti.

Prvi verovatni podatkovni red određuje kraj višerednog zaglavlja; četvorocifrena
godina sama nije dovoljna za numerički podatak. Tabele se klasifikuju kao
`data`, `text`, `navigation`, `list` ili `unknown`. Navigation tabele se čuvaju
u processed evidence, ali se ne indeksiraju; nejasne tabele se zadržavaju kako
se dokaz ne bi izgubio.

Na korpusu je zabeleženo 1.005 fizičkih HTML fragmenata i 978 logical tables,
od kojih 965 ulazi u retrieval, a 13 navigation tabela ne proizvodi chunk.
Processed QA beleži 1.005 validnih Markdown renderinga i 183 normalization
fallback-a; chunk scope beleži 182. Ta dva broja nisu ista populacija.

## 6. Chunking

`src/chunking/chunk_documents.py` zahteva token-based `length_function` i čuva
tokenizer/reviziju u konfiguraciji. Izabrana politika je:

| Parametar | Promovisana vrednost | Izvor |
|---|---:|---|
| Strategija | RecursiveCharacterTextSplitter | `CLEANING_AND_cHUNKING_REPORT_FINAL.md` |
| Narativna veličina | 500 tokena | v19 manifest/chunk config |
| Konfigurisani overlap | 32 tokena | v19 manifest/chunk config |
| Separators | `\n\n`, `\n`, razmak, karakter | chunking report |
| Tabela | jedna kompletna logical table | chunking report |

Recursive splitter prvo pokušava granicu pasusa, zatim reda, reči i karaktera.
Narativ se ne meša između `section_path` vrednosti, a section prefix ulazi u
budžet. Za svaki chunk čuvaju se source character i token spanovi, block ID,
section, content type i filing metadata. Chunk ID zavisi od konfiguracije; zato
postoje odvojene gold mape za 250/500 tokena, a stari character baseline ostaje
istorijski.

Benchmark na MBLY i TSLA poređivao je recursive/fixed strategije za 128, 192,
250 i 500 tokena. Sve uspešne konfiguracije imale su 100% coverage blokova,
100% section accuracy i 100% tabelarni kontekst. Na primer, MBLY recursive
500/64 imao je 468 chunk-ova, medianu 253,5 tokena i boundary accuracy 92,1%,
naspram fixed 500/64 sa 433 chunk-a i 61,7%. TSLA recursive 500/64 imao je 343
chunk-a i 89,1%, naspram fixed 334 i 66,9%. Recursive 500 je izabran zbog boljih
semantičkih granica, iako fixed preciznije ostvaruje nominalni overlap.

Stvarni overlap se meri odvojeno od konfigurisanog overlap-a. Prirodna granica
često znači da je izmereni overlap nula; separator-aware algoritam ne garantuje
32 tokena u svakom paru. Tabele su izuzete od narativnog limita: svaka zadržana
logička tabela ostaje potpuna u jednom tabelarnom chunk-u, zajedno sa headerima,
redovima, jedinicama i izvornim indeksima. Promovisani corpus ima 3.561 narativni
i 965 tabelarnih chunk-ova, ukupno 4.526.

Ograničenja: parser radi nad stvarnim HTML `<table>` elementima, ne zaključuje
tabele iz vizuelno poravnatih pasusa, nastavci kroz susedne HTML tabele mogu
ostati odvojeni, naslov može biti nepoznat, a `page_start`/`page_end` nisu
popunjeni.

## 7. Embeddings

Eksperimenti su uključivali MiniLM i BGE-base; podrška postoji i za druge
lokalno odabrane modele kroz `src/embeddings/embed_chunks.py`. Promovisani
embedder je `BAAI/bge-base-en-v1.5` sa tačnom revizijom
`a5beb1e3e68b9ab74eb54cfd186867f64f240e1a`.

| Podešavanje | Vrednost |
|---|---|
| Dimenzija | 768 |
| Normalizacija | da |
| Query prefix | `Represent this sentence for searching relevant passages: ` |
| Document prefix | prazan |
| Manifest | manifest-v3, poravnanje sa chunk redosledom |
| Korpus | 4.526 vektora za 4.526 chunk-ova |

Na 300-question benchmarku historical MiniLM dense run imao je Recall@10
0,5472 i MRR@10 0,3171. BGE-base v2/v3 je imao Recall@10 0,7072, hit@10
0,7967, complete@10 0,6167 i MRR@10 0,5168. MiniLM je bio brži/jeftiniji za
lokalno računanje, ali je BGE imao jasnu retrieval prednost. Svi embedding
manifest-i sadrže source hash, model revision, dimenziju, normalizaciju,
input truncation informacije i vektorsko poravnanje. Izbor je zato kvalitetno
promovisan BGE, uz reproduktivni NPZ fallback.

## 8. Retrieval

Filingski retrieval je implementiran u `src/retrieval/dense.py`,
`src/retrieval/scope_aware.py`, `src/retrieval/evidence_policy.py` i
`src/indexing/qdrant_index.py`. Dense pretraga koristi normalizovane BGE vektore;
BM25 je lexical path; hybrid kombinuje rangove reciprocal-rank fusion (RRF).
Aktivni v19 parametri su `rrf_k=100`, `candidate_k=50`, `final_evidence_k=10`,
minimum dva dostupna chunk-a po subquery-ju i multi-subquery bonus `0,01`.

Notebook `notebooks/hybrid_rag_generation.ipynb`, evaluator
`src/scripts/evaluate_scope_aware_hybrid_retrieval.py` i deployed API koriste
isti scope-aware entry point. Originalni query ostaje nepromenjen. Planner pravi
atomic subqueries; company/ticker/alias matching i Comparison Cues određuju
scope. Candidate pool se filtrira po ticker-u, merge/dedup radi po stabilnom ID-u,
a izbor radi u rundama da svaka subquery dobije najmanje dva chunk-a. Preostala
mesta popunjavaju se po RRF rezultatu i bonusu. Finalni context je najviše 10
jedinstvenih chunk-ova; balanced partial evidence se zadržava kada puna kvota ne
može da stane.

### Retrieval metrike

| Eksperiment | N | Recall@10 | Hit@10 | MRR@10 | Napomena |
|---|---:|---:|---:|---:|---|
| MiniLM dense, v2 | 300 | 0,5472 | 0,6300 | 0,3171 | historical dense baseline |
| BGE-base dense, v3 | 300 | 0,7072 | 0,7967 | 0,5168 | final embedder, dense-only |
| BM25, v3 | 300 | 0,7517 | 0,8400 | 0,5428 | lexical-only corpus benchmark |
| BGE/BM25 RRF | 300 | 0,8117 | — | 0,6157 | historical hybrid result in `INTERNSHIP_REPORT.md` |
| Scope-aware hybrid | 300 | 0,8411 | — | 0,6157 | historical promoted comparison; scope-aware path |
| Retriever-only finalization | 64 scored | hit@50 0,8594 | — | MRR@50 0,5051 | v1 frozen release baseline |

Prve tri dense vrednosti dolaze iz `data/evaluation/dense_retrieval/` i
`data/evaluation/bm25_retrieval/`; hybrid vrednosti su sačuvane u
`INTERNSHIP_REPORT.md` kao istorijski corpus-wide comparison i ne treba ih
mešati sa 75-case end-to-end gate-om. Na Mobileye-only dense baseline-u od 60
pitanja Recall@10 je 0,6167, MRR@10 0,4446 i hit rate 0,6833. Drugi istorijski
34-question subset ima Recall@10 0,7206; drugačiji query set znači da rezultati
nisu direktno zamenljivi.

Pre-balanced P0 selector je imao candidate recall 1,0, ali final recall 0,5887.
Promovisana company-balanced politika je na tom frozen five-case gate-u imala
candidate recall 1,0, final recall 1,0, quota satisfaction 1,0 i source-display
exactness 1,0; ovo je mali P0 gate, ne corpus-wide generalizacija.

Qdrant je v19 primary storage: server 1.18.2, Python client 1.19.0, alias
`ava_filing_chunks_current`, fizička kolekcija
`ava_filing_chunks_89d3a5be9e7d7a8e`, 4.526 tačaka, 768 dimenzija i Dot distance.
Audit je prošao. Lokalni NPZ/BM25 path ostaje fallback/shadow reproducibility
oracle; pri konfigurisanom primary Qdrant kvaru readiness pada i sistem ne prelazi
na mock odgovor. Qdrant parity je prihvaćen za svih 11 dense cases i tri
final-selection cases u release dokumentaciji.

## 9. Generation i citations

`src/generation/prompts.py`, `src/generation/service.py`,
`src/generation/provider.py`, `src/generation/citations.py` i
`src/orchestration/task_execution.py` grade grounded generation. Prompt označava
filing excerpts kao untrusted evidence, a short-term context i memory kao
untrusted user context. Svaka factual claim treba neposredan tačan source ID u
uglaste zagrade. Citation parser prihvata samo ID-eve iz finalnog generation
context-a; nepostojeći, malformirani ili neupotrebljeni ID-evi se odbacuju.
Ako nijedan citation ne može da se razreši, source lista je prazna.

Tabele se prosleđuju kao strukturirani headers, rows i units, a frontend ih
prikazuje iz schema-v2 podataka, ne rekonstruiše ih iz Markdown-a. Prompt
razlikuje total, subtotal, procenat, period, jedinice i valutu. Evidence-derived
calculations se izvode tek posle validacije operandâ; model ne bira rezultat kao
autoritet. Ako deo multi-part pitanja nema dokaz, podržani deo se može odgovoriti,
a nepodržani deo se eksplicitno označava kao nedostupan.

Serbian generation koristi odvojeni prevodilački korak koji čuva citation ID-eve
i redosled tvrdnji. U v16 language parity testu, 10 parova je imalo company
resolution, route, gold recall i numerical parity 1,0; citation-ID parity je
0,8 i to je eksplicitno prihvaćen release exception.

### Generation metrike i ograničenja

Oracle-context tri-run evaluacija za 75 slučajeva imala je invalid citations 0,
source-display exactness 1,0 i abstention accuracy 0,9067. U realnom end-to-end
v1 run-u diagnostic LLM judge (GPT-4o) na 225 records zabeležio je claim
correctness 0,8133, faithfulness 0,8000, citation support 0,7956, abstention
0,7600, relevance 0,8356 i conciseness 0,8178. Judge je dijagnostički; kontrolna
single-human review od 20 answer pairs dala je pass 1,0 za correctness,
faithfulness, citation support, abstention i relevance, ali nije imala drugog
reviewer-a i nije sertifikovala kompletne excerpts. Zato se ta dva rezultata ne
spajaju u jednu “tačnost”.

End-to-end latency iz v1 tri realna run-a: prosečno 3.028–4.350 ms po run-u,
p95 5.490–5.732 ms, sa jednim maksimumom 93.774 ms. Gateway usage/cost nije
kompletno dostupan; provider usage se beleži kada ga provider vrati, a cenu ne
izmišljamo. V19 freeze koristi Azure-compatible Chat Completions i
`llm_streaming=false`; ako provider vrati samo buffered JSON, AVA šalje jedan
kompletan `delta`, bez lažnog token-by-token kucanja.

## 10. Metodologija evaluacije

Evaluacije su slojevite i koriste frozen inputs, manifest hash-eve, source commit,
model, corpus/index, prompt i parametre. Phase plan zabranjuje tuning na holdout-u:
development služi za prompt rad, validation za promociju, a holdout samo za
završni izveštaj.

| Sloj | Veličina | Šta proverava | Izvor |
|---|---:|---|---|
| Corpus retrieval | 300 | dense/BM25 Recall, Hit, Complete, MRR, latency | `data/evaluation/dense_retrieval/`, `bm25_retrieval/` |
| QA/generation | 75 | kategorije: factual, synthesis, comparison, table, absent, follow-up, web | `data/evaluation/finalization/v1/qa_gold.jsonl` |
| Agent route/tool | 60 | route, ticker, tools, freshness, source keys | `data/evaluation/finalization/v18/...` |
| Conversation | 9 turns + 4 state cases | follow-up, topic switch, recall, delete, isolation | `v18/runs/conversation-history-v1` |
| Memory | 20 | explicit/summary, edit, delete, relevance, ownership | `v1/memory.jsonl` i release runs |
| Security | 8 direct live + dokument boundary tests | injection, prompt/ID/secret leakage | `v1/runs/security-direct-live-v1` |
| Language | 10 paired prompts | resolution, route, recall, numbers, citations | `v16/runs/language-parity-v1` |
| Frontend | 43 Vitest + lint/type/build | UI/API/accessibility contracts | v19 phase6 ledger |

Deterministički testovi i ljudski label-i su autoritativni za route, ownership,
citations, numbers i security. LLM judge je samo dijagnostika. Za failure se
prvo pita da li je dokaz retrieval-ovan; ako nije, problem je corpus/index/parser/
chunking/retrieval, a ako jeste, problem je generation/prompt/grounding.

V19 release gates: focused backend 151 passed, frontend lint passed, 43 frontend
tests passed, TypeScript passed, production build passed, 60/60 route/tool cases
passed, conversation-history gate passed i freeze validation passed. V19 phase6
ledger takođe beleži raniji complete suite `446 passed, 3 skipped, 267 subtests`.

## 11. Routing i bounded planning

Istorijski sistem je koristio više preklapajućih deterministic route checks,
route LLM, retrieval planner i memory-scope inference. To je dovodilo do gubitka
jedne kompanije, odbacivanja web dela mešanog pitanja i pogrešnog calculator
poziva. `LLM_ROUTER.md` i `LLM_ROUTER_REFACTOR.md` definišu finalni dizajn:
jedan bounded LLM JSON planner i server-side typed validation.

Planner dobija originalni query, canonical companies/aliases, server-owned chat
scope, bounded short-term turns, validirani singular follow-up ticker, owner-scoped
memory candidates, matching upload excerpts i capability limits. Plan sadrži
original query, memory references, do četiri task-a i dependencies. Task vrste
uključuju filing retrieval, upload retrieval, trusted web, direct/evidence
calculation, conversation i clarification.

Server pre izvršavanja proverava schema, task IDs, dependencies, ticker enum,
memory ownership, source keys, URL policy, task/tool limits i ambiguity. Dozvoljene
su samo uske normalizacije: jedan spoljašnji JSON Markdown fence, implicitni
`sec_edgar` key i poznati filing-shaped current-web oblik. Server ne popravlja
arbitrarne URL-ove, scope, IDs ili budžete.

Implementirane popravke obuhvataju `their`/`both` follow-up, singular pronoun
context, gubitak kompanija u comparison-u, possessive tickers, product aliases,
CEO letter-count false positive, stale web fallback, upload pre-search i
typed memory relationships. Mixed filing/web i filing/upload zadaci mogu se
izvršavati bounded parallelism-om; calculator čeka validirane evidence dependency.
Ako jedan task ne uspe, raspoloživi dokaz ostaje, a nedostajući deo se ne
izmišlja.

Preostala ograničenja su planner context resolution kod nekih ambiguous follow-up
upita, provider planovi koji mogu biti malformed ili over-budget i činjenica da
trusted web rezultat može biti nedostupan. U tim slučajevima bezbedno odbijanje
je očekivano ponašanje, ne dokaz da činjenica ne postoji.

## 12. Web stack i calculator

`src/tools/web_search.py` je provider-neutral adapter sa Tavily Search API-jem.
Trusted source registry dozvoljava SEC, issuer official, NHTSA, exchange, Robinhood
i Reuters kategorije, ali planner bira samo source keys, ne proizvoljne domene.
Ticker se rešava pre query rewrite-a; market query je npr. `TSLA current stock
price`. Result mora koristiti HTTPS i odobren host. Redirects se ne prate
automatski; lokalne/private IP adrese, credentials, nebezbedni portovi i
neodobreni URL-ovi se odbacuju. Excerpt je ograničen, HTML se čisti, a web tekst
se tretira kao untrusted data.

Current quote zahteva kvalifikovan izvor, publisher timestamp, retrieval timestamp,
market status i disclosed delay. Ako takav rezultat ne postoji, AVA kaže da
verifikacija nije uspela umesto da koristi zastareli filing ili nepovezan članak.
V18 manifest: 60/60 route/tool cases, web-required recall 1,0, unnecessary web
call rate 0 i calculator false positives 0; live Tavily smoke je vratio tri
allowlisted Tesla investor-relations rezultata.

`src/tools/calculator.py` koristi `Decimal`, ograničava dužinu izraza, dubinu,
broj operacija i decimalna mesta i ne izvršava Python/kod. Podržava genuine
arithmetic i evidence-derived operands. Ne računa ponavljanje, enumeraciju,
letter/name manipulation ili prosto prepisivanje broja. Historical Phase 10
calculator regression je 10/10, exactness 1,0, ali v19 effective configuration
ga uključuje samo kada je deployment capability eksplicitno aktivirana i
validirana; provider-native function calling nije potreban.

## 13. Istorija razgovora, upload-i i long-term memory

`src/conversations/` koristi PostgreSQL kao canonical owner-scoped storage za
tenant/user, conversations, messages, summaries, source uses, feedback, pins,
company scope, memory preferences i auth sessions. Browser storage nije trust
boundary. Short-term context je bounded, extractive, newest-first sa
chronological restoration; aktivni turn se izuzima, oversized turn se preskače,
a summary se rebuild-uje.

Long-term memory je derived semantic index u posebnom Qdrant collection-u. V19
manifest navodi top-k candidate 5, similarity threshold 0,55 i token budget
1.024. PostgreSQL je source of truth; Qdrant se sinhronizuje, a edit/delete
menja oba sloja. Memory write je explicit opt-in za stabilnu preference/profile
informaciju; memory ne može biti evidence za CEO, revenue, market price ili
filing claim. Memory se planneru daje kao untrusted context, typed po relaciji
(`preferred company`, `favorite CEO`, `preferred metric` itd.).

V18 conversation evaluator: query-only accuracy 0,6667, contextual accuracy
0,8889, history-dependent query-only 0,0 naspram contextual 1,0, delta 1,0;
standalone contextual accuracy 0,75, topic-switch contextual accuracy 1,0,
planner errors 0. Četiri state slučaja (delete conversation, delete all,
cross-user isolation, cross-conversation isolation) su prošla.

Upload pipeline u `src/documents/` prihvata PDF i text, čuva originalne bytes i
source text, obrađuje ih kroz owner/chat-scoped Qdrant collection i prikazuje
izvor. Upload pre-search postoji, ali relevance bridge mora biti dovoljno jak da
ne preotme nepovezan filing upit. Instruction-like sentences se quarantinuju
samo u provider-facing excerpt-u; originalni source ostaje nepromenjen.

Ovo je bitna hijerarhija: filing/upload/web su evidence uz provenance; memory i
conversation su untrusted context; model output je niže od svih tih izvora i
nikada ne postaje evidence.

## 14. Frontend i korisničko iskustvo

`src/frontend/src/App.tsx`, `src/frontend/src/styles.css`, `src/frontend/src/i18n.ts`
i React/TypeScript komponente čine AVA interfejs. Podržani su responsive desktop
i mobile layout, light theme sa belom pozadinom, dark theme sa vrlo tamno-plavom
pozadinom, keyboard navigation, focus states, semantic controls, reduced-motion
pravila i nenametljivi live status regioni. Kanonski avatar je `src/frontend/avatar/ava.png`,
a odobrene teme koriste `ava-light.png` i `ava-dark.png`; favicon je supplied
`favicon.png`. AVA identitet je restriktivan i ne uvodi izmišljenu biografiju ili
mascot behavior.

UI ima levi sidebar sa recent/pinned chatovima, company-scope selector, Settings
za General, Memory i Personalization, model/language izbor, upload meni za PDF i
pasted text, Sources panel, citations, thinking/status evente i feedback. Serbian
i English UI strings postoje u `i18n.ts`. Assistant avatar je uz assistant
poruku, ne uz user poruku. Markdown se renderuje bez unsanitized HTML-a. Source
cards imaju filing narrative/table schema, web ili upload provenance; raw internal
chunk IDs nisu primarni korisnički label.

Traženi vizuelni dokazi nisu supplied u repozitorijumu, pa su navedeni kao
placeholders:

[SCREENSHOT PLACEHOLDER: prazan AVA chat sa avatarom, input poljem, upload dugmetom i bez izvora; pokazuje početno stanje.]

[SCREENSHOT PLACEHOLDER: levi sidebar sa recent i pinned chatovima, aktivnim razgovorom i New chat kontrolom.]

[SCREENSHOT PLACEHOLDER: company-scope selector sa All companies i izabranim ticker-ima.]

[SCREENSHOT PLACEHOLDER: Settings → General sa theme, language i answer-model kontrolama.]

[SCREENSHOT PLACEHOLDER: Settings → Memory sa enable, list, edit i delete kontrolama.]

[SCREENSHOT PLACEHOLDER: Settings → Personalization sa nickname, warmth, enthusiasm, emoji i custom-instructions poljima.]

[SCREENSHOT PLACEHOLDER: upload meni koji nudi PDF/text upload i pasted-text flow.]

[SCREENSHOT PLACEHOLDER: pasted-text upload dijalog sa filename poljem, tekstom i potvrdom.]

[SCREENSHOT PLACEHOLDER: Sources panel sa filing narrative i strukturiranom tabelom.]

[SCREENSHOT PLACEHOLDER: citirani filing odgovor sa citation markerima i odgovarajućim source card-ovima.]

[SCREENSHOT PLACEHOLDER: citirani web odgovor sa publisher-om, URL-om i retrieval timestamp-om.]

[SCREENSHOT PLACEHOLDER: calculator odgovor sa jasno prikazanom Decimal formulom i rezultatom.]

[SCREENSHOT PLACEHOLDER: Serbian-language odgovor koji zadržava citation markere.]

[SCREENSHOT PLACEHOLDER: long-term-memory recall uz jasno odvojen lični kontekst od filing evidence.]

[SCREENSHOT PLACEHOLDER: mixed filing/web odgovor ako je dostupan, sa odvojenim filing i web source karticama.]

[IMAGE PLACEHOLDER: AVA interfejs sa citiranim SEC filing odgovorom.]

[DIAGRAM PLACEHOLDER: SEC ingestion → parsing → chunking → embeddings → hybrid retrieval → grounded generation.]

[DIAGRAM PLACEHOLDER: bounded planner koji usmerava filing, upload, web, memory i calculator task-ove.]

[DIAGRAM PLACEHOLDER: evidence hierarchy i citation validation flow.]

## 15. Deployment, observability i security

`start_app.sh` pokreće lokalni PostgreSQL i Qdrant, inicijalizuje/audit-uje
filing collection, čeka readiness API-ja i pokreće Vite. FastAPI je u
`src/backend/`, frontend je Vite aplikacija, a PostgreSQL migrations su u
`src/conversations/migrations/`. Qdrant filing collection i memory collection
su odvojene. V19 readiness zahteva usklađene artifact counts i Qdrant alias.

Environment konfiguracija je backend-only: SEC user agent, model provider,
Tavily key, PostgreSQL DSN, Qdrant URL/alias, tenant/user boundary i tool limits.
API key, gateway headers, prompt, retrieval scores, stack traces i raw provider
errors ne odlaze u browser. `/api/ready` proverava stvarnu zavisnost; Qdrant
kvar u primary režimu nije skriven mock fallback-om.

`src/observability/request_trace.py` beleži request ID, original query, resolver,
subqueries, candidate provenance, quota/token allocation, final evidence, answer,
citations, stage latency, time-to-first-token, provider usage, cancellation i
redigovan error class. Browser vidi samo opaque `X-Request-ID`. `OBSERVABILITY.md`
navodi retention default 30 dana za spoljašnji log sink; logovi mogu sadržati
 pitanja i odgovore i moraju biti access-controlled.

Security model koristi OIDC-derived tenant/user u produkciji, owner predicates u
PostgreSQL-u i tenant/user Qdrant filtere. Upload limit je 20 MiB, request body
limit 16 KiB, postoje rate limits, timeout-i, bounded retries i circuit breaker.
React Markdown isključuje HTML, source schemas su fixed, a citation filtering
sprečava raw ID disclosure. Raw uploads ostaju zaštićeni. PostgreSQL i Qdrant
nisu javno izloženi; deployment zahteva HTTPS, secure cookies, CSRF, secret
rotation, backup/restore drill i image/dependency scanning.

## 16. Greške, debugging i popravke

| Uočeni problem | Najranija faza | Uzrok | Promena | Verifikacija | Preostalo ograničenje |
|---|---|---|---|---|---|
| `td` headers, pogrešni balance/year redovi | parsing | idealizovani parser | span-aware table-schema-v2 i numeric data-row heuristic | table fixture testovi; processed QA | budući filing layout-i zahtevaju proveru |
| Velike tabele seku se po redovima | chunking | narativni limit primenjen na tabele | jedna kompletna logical table po chunk-u | chunk QA, 965 table chunks | vrlo velike tabele imaju veći chunk |
| Character chunk IDs zastarevaju | evaluation | promena veličine menja sekvencijalne ID-eve | config-specific gold mape i hashes | chunk/evaluation tests | legacy rezultate treba čitati kao historical |
| MiniLM slabiji retrieval | embeddings | slabija semantička reprezentacija | BGE-base v1.5 promocija | 300-case benchmark | BGE je English-focused |
| Kandidati pronađeni, ali final evidence nepravedan | selection | fixed top-10 favorizuje jednu kompaniju | balanced per-company quota i min 2/subquery | P0 candidate/final recall 1,0 | quota može biti nemoguća kod malog pool-a |
| Fale pojedine kompanije u comparison-u | planner | više nezavisnih route/planner odluka | atomic task plan, final ticker validation | v18 60/60; v1 historical failures | ambiguous context može biti odbijen |
| `their`/pronoun follow-up gubi scope | routing | query-only plan bez validiranog context-a | singular prior-answer ticker i bounded context | v18 history contextual 1,0 | samo unambiguous singular reference |
| `Super Cruise` nije bio GM alias | resolution | alias coverage nije ažuriran | alias test i frozen labels | Phase 1 six-failure repair |
| current request pada u filing fallback | routing | web-disabled fallback nije bio fail-closed | stale fallback fix | Phase 1; v18 web-required recall 1,0 |
| CEO name/letter count pokreće calculator | calculator route | keyword false positive | genuine arithmetic classifier | 60 route cases; false positives 0 |
| Calculator je ranije bio nedostupan u frozen runtime-u | capability | fail-closed disabled config | Decimal path i explicit capability gates | historical 10/10 regression; v19 config |
| Brave provider više nije aktivan | web | provider promenjen | direktan Tavily adapter + registry | live smoke, v18 route gate | rezultat zavisi od qualifying provider-a |
| web instructions mogu uticati na model | security | web tekst tretiran kao instrukcija | source quarantine, URL/domain screening | direct security 8/8 safe |
| citation display je ranije vraćao previše izvora | citations | fallback na sve retrieved chunks | cited-only resolution | P0 0,4286 historical → v18 gate pass |
| Serbian citation set nije uvek identičan | localization | prevod/selection može promeniti ID set | English planning + citation-preserving translation | v16 citation parity 0,8 | prihvaćena release exception |
| history-only planner ne rešava follow-up | conversation | nema prethodnog turna u query-ju | bounded recent context + extractive summary | history delta 1,0 |
| memory meša favorite CEO i preferred company | memory | semantički match bez typed relationship | planner memory_resolution i owner filter | v18 memory/state gate |
| upload preuzima nepovezan filing upit | upload routing | nearest match bez relevance bridge-a | server-side pre-search i threshold | route manifest upload cases |
| empty chats su se nepotrebno kreirale | UI/history | kreiranje pre prvog sadržaja | defer empty conversation creation | frontend/backend tests |
| shared model selection je mogla da race-uje | backend | `self.generator.model` mutacija | request-scoped model argument | Phase 1 mutation scan zero |
| runtime prompt branch je bio kontradiktoran | generation | legacy strict-abstention flag | uklonjen rejected production branch | Phase 1 tests i commit `9654d73` |
| provider nema native tools/strict JSON | provider | gateway capability mismatch | server-side typed executor i safe normalization | capability probes, route tests |
| buffered gateway nema genuine streaming | delivery | provider vraća kompletan JSON | buffered `delta`, bez fake typing | SSE contract tests |

Commit history pokazuje ovu evoluciju kroz npr. `9059544` (Tavily), `97df606`
(typed plans), `6073264` (settings), `1c62d8b` (request model isolation),
`4dc016e`/`7f23dde`/`4f62c3d` (boundary refactors), `aeba7cb` (memory controls),
`379e877` (bounded task execution) i `56d3ae6` (pronoun follow-up fix).

## 17. Arhitektonske odluke i trade-off-i

| Odluka | Alternativa | Dobit | Cena/ograničenje |
|---|---|---|---|
| SEC-hosted HTML | PDF/sekundarni sajt | canonical provenance i struktura | HTML layout je nepravilan |
| Immutable raw | overwrite latest | validne poredbene evaluacije | snapshot se ručno osvežava |
| Structured blocks | jedan plain text | section/order/table provenance | složeniji parser |
| Logical tables | row fragments/Markdown-only | kompletni headers, units i rows | veći tabelarni chunk |
| Recursive token chunks | fixed/character chunks | bolje semantičke granice | actual overlap nije garantovan |
| BGE-base | MiniLM | bolji Recall/MRR | 768D i veći compute |
| Hybrid RRF | dense-only | lexical + semantic komplementarnost | dodatni index i tuning |
| Qdrant | samo NPZ/FAISS | persistent filtered vector service | operativna zavisnost i parity audit |
| FastAPI + React | notebook/UI-only | odvojeni API i accessible browser UX | više deployment komponenti |
| PostgreSQL history | browser/local memory | server ownership i deletion | DB lifecycle/retention posao |
| Semantic memory | full transcript | preference recall uz bounded context | untrusted derived index |
| Decimal calculator | model arithmetic | deterministic exact result | samo allow-listed genuine arithmetic |
| Tavily trusted registry | arbitrary web | freshness uz source controls | provider/key/result availability |
| Bounded LLM planner | open-ended agent | mixed tasks uz finite execution | planner može pogrešiti ili odbiti |
| Server validation/ownership | frontend assertions | tenant, scope i tool safety | backend je složeniji |
| Evidence-first generation | free-form model answer | grounding, abstention i source truth | veći prompt/context i latency |

Open-ended autonomous agent i Graph RAG su svesno odbačeni. Bounded plan je
dovoljan da poveže filing, upload, web, calculator, conversation i clarification,
ali ne dozvoljava proizvoljne akcije ili kod.

## 18. Budući planovi i moguće optimizacije

Već autorizovan budući rad treba odvojiti od spekulacije. Kandidati koji su
navedeni u finalization dokumentaciji, ali nisu deo frozen release-a, uključuju:

- još bolji planner context resolution i typed memory references;
- stabilniji mixed-source filing/web planovi i jači quote provideri;
- meren cross-encoder reranking, prvo Mobileye-only, uz zaštitu BGE baseline-a;
- bolji multilingual/Serbian retrieval;
- veći i nezavisniji QA/generation set, više ljudskih review-era i inter-reviewer agreement;
- concurrency-safe per-request model selection i dalju modularizaciju velikih facade modula;
- bogatiji source editing i UI polish;
- potpuni production OIDC, retention, backup/restore i horizontal rate limiting;
- filing-image ingestion, koja je u Phase 6 eksplicitno preskočena.

Ovo nisu promovisane karakteristike. Ne treba uvoditi Qdrant sparse fusion,
reranker, image retrieval ili open-ended tools bez pre/post evaluacije, rollback
plana i novog freeze manifest-a. Cost, token usage i provider streaming takođe
treba meriti pre optimizacije; ne smeju se zaključivati iz prosečnog latency-ja.

## 19. Zaključak

Frozen v19 AVA release candidate pruža proverljiv RAG sistem nad 11 SEC 10-K
filings: immutable acquisition, structure-preserving parser, logical-table
preservation, recursive token chunking, aligned BGE embeddings, hybrid retrieval,
Qdrant persistence, bounded planning, grounded generation, citation filtering,
Tavily web, Decimal calculation, owner-scoped history, upload evidence, semantic
memory i React/FastAPI interfejs.

Najčvršći rezultati su corpus reproducibility, 4.526 usklađenih chunks/vectors,
Qdrant audit, 300-case retrieval gains, balanced P0 evidence selection, 60/60
v18 route/tool gate, history-dependent contextual improvement i v19 backend/
frontend release gates. Najvažnije granice su ograničena veličina evaluation
setova, dijagnostički status LLM judge-a, provider-dependent web dostupnost,
buffered umesto token streaming-a, prihvaćena Serbian citation parity 0,8,
planner ambiguity i operativna složenost velikih runtime facades.

AVA zato demonstrira inženjerski princip važniji od same fluentnosti: odgovor je
koristan tek kada se može povezati sa tačnim ulazom, retrieval odlukom,
validiranim dokazom, kontrolisanim alatom i bezbedno prikazanim izvorom.

---

# Proširena tehnička razrada

Sledeće stranice predstavljaju detaljniju metodološku i implementacionu razradu
prethodnih poglavlja. Sažeti deo iznad daje orijentaciju, dok ovaj deo eksplicitno
razdvaja istraživačke pretpostavke, implementacione odluke, eksperimentalne
rezultate, greške i granice zaključivanja. Brojevi su preuzeti iz sačuvanih
artefakata; kada rezultat pripada starijem kandidatu ili manjem skupu, to je
navedeno uz sam rezultat.

## 1A. Uvod: problem kao lanac verifikacije

Klasičan chatbot ocenjuje se uglavnom po tome da li odgovor zvuči relevantno.
Za SEC analizu to nije dovoljno. Godišnji izveštaj sadrži desetine hiljada
rečenica, finansijske tabele, posebne jedinice, više fiskalnih perioda i
ponovljene termine. Jedna numerički uverljiva, ali pogrešno pripisana vrednost
može biti ozbiljnija greška nego kratak odgovor koji otvoreno kaže da dokaz nije
pronađen. Zbog toga je AVA projektovana kao lanac sa odvojenim ugovorima.

| Ugovor | Ulaz | Izlaz | Kako se proverava |
|---|---|---|---|
| Acquisition | SEC submissions + CIK | immutable HTML + metadata | form, accession, URL, hash |
| Parsing | HTML snapshot | ordered typed blocks | required fields, order, provenance |
| Chunking | blocks + versioned config | narrative/table chunks | coverage, section, table completeness |
| Embedding | ordered chunks | aligned vectors + manifest | count, shape, norm, source hash |
| Retrieval | query + scope | candidates/ranks | Recall, Hit, MRR, gold survival |
| Evidence policy | candidates + token budget | final context | quota, deduplication, token packing |
| Generation | original query + context | answer + citation IDs | citation resolution, support labels |
| Presentation | validated result | browser source cards | frontend schema, no internal leakage |

Ovakva dekompozicija je namerno skuplja od jedne pipeline funkcije. Dobit je u
diagnostici: ako odgovor nema dokaz, moguće je pitati da li je HTML izgubljen,
blok pogrešno klasifikovan, chunk izbačen, vektor neusklađen, kandidat pogrešan,
evidence selector nepravedan ili je model imao dovoljan dokaz ali ga nije
upotrebio. Bez te separacije svaka greška bi izgledala kao “LLM hallucination”.

Druga centralna odluka je da se korisnički zahtev ne menja u transcriptu.
Planner sme da napravi interni query poput `TSLA current stock price`, ali se
originalna formulacija zadržava u PostgreSQL-u i request trace-u. Time su
sačuvani audit, reprodukcija i mogućnost da se proveri da li je preformulacija
promenila značenje.

Treća odluka je negativna: model nije trust boundary. Model može predložiti plan,
ali ne može sam izabrati neodobreni ticker, memory ID, URL, kalkulaciju ili
source. Isto važi za upload i web tekst: oni mogu sadržati instrukcije, pa se
tretiraju kao podaci, nikada kao sistemske naredbe.

## 2A. Korpus: izbor, snapshot i statistička interpretacija

Korpus nije zamišljen kao kompletna baza svih kompanija koje razvijaju
autonomna vozila. On je eksperimentalni okvir. Uključuje proizvođače vozila,
dobavljače senzora, čipove i platforme sa dovoljno javnih SEC disclosure-a da se
mogu postavljati pitanja na nivou poslovanja, rizika, proizvoda, finansija i
uporedne analize.

Najvažnija posledica fiksnog korpusa je da “company scope” ima formalno značenje.
Ako korisnik navede `Tesla`, planner ne sme proizvoljno proširiti zahtev na celu
industriju. Ako navede više kompanija, svaka mora preživeti plan i evidence
allocation, osim ako korisnik nije postavio konfliktan ili nejasan uslov.

### Snapshot metadata

| Polje | Razlog čuvanja | Upotreba u runtime-u |
|---|---|---|
| `company` | čitljiv naziv | source label i filter |
| `ticker` | kanonski identitet | scope, Qdrant payload, resolver |
| `cik` | SEC identitet | acquisition i validacija |
| `form` | razlikovanje 10-K/10-K/A | primary filing guard |
| `filing_date` | datum objave | provenance i prikaz |
| `reporting_period` | fiskalni period | odgovor i numerička validacija |
| `accession_number` | stabilni SEC filing identitet | URL i reproducibility |
| `source_url` | direktni izvor | source card |

`latest` se ne čita kao promenljivi runtime rezultat. Acquisition u trenutku
preuzimanja bira latest normal 10-K, ali jednom sačuvan snapshot ostaje ulaz
za sve kasnije eksperimente. Kada se pojavi noviji filing, on se čuva kao novi
artifact; stari se ne pregazi. To je uslov za poštene pre/post rezultate.

### Zašto ne PDF kao primarni ulaz?

SEC-hosted HTML je izabran zato što ima originalne DOM odnose, Inline XBRL
vidljiv tekst i direktnije source provenance. PDF bi bio koristan za vizuelnu
verifikaciju i buduću image fazu, ali ekstrakcija redova, kolona i anchors iz PDF-a
uvodi novi sloj grešaka. Phase 6 filing-image ingestion je eksplicitno preskočen;
izveštaj ne tvrdi da AVA rešava slike ili grafikone.

## 3A. Motivacija: autonomna vožnja kao analitički use case

Autonomous-driving kompanije ne predstavljaju homogenu kategoriju. Jedna firma
može prijaviti prihode po automobilskom segmentu, druga po senzorskom proizvodu,
treća po data-center platformi, a četvrta uglavnom po istraživačko-razvojnoj
aktivnosti. Termini kao “autonomous”, “driver assistance”, “lidar”, “EyeQ”,
“vehicle platform” i “automotive” mogu pripadati različitim delovima izveštaja.

To motiviše dva tipa pitanja:

1. **Within-company**: šta kompanija tvrdi o poslovanju, proizvodima, rizicima,
   segmentima ili finansijskim rezultatima?
2. **Cross-company**: koje su razlike između kompanija kada se usklade period,
   metrika, jedinica i nivo disclosure-a?

Drugi tip zahteva više od top-1 semantic search-a. Potrebno je sačuvati company
scope, dati svakoj kompaniji dokazni budžet i sprečiti da jedan dokument popuni
ceo prompt. Odatle dolaze Comparison Cues, atomic subqueries i round-robin
selection.

Važno je odvojiti motivacionu hipotezu od rezultata. Projekat ne meri “rast
industrije” niti tvrdi da je bilo koja kompanija najbolja. On meri kvalitet
inženjerskih komponenti na frozen filing corpus-u i frozen evaluation setovima.

## 4A. Acquisition implementacija i operativni tok

Acquisition ima tri faze: registry resolution, SEC submission lookup i content
download. Registry u `src/filings/corpus.py` mapira naziv/ticker/CIK. Lookup u
`src/filings/fetch_data.py` filtrira exact `10-K`, a ne fuzzy sličan form. Tek
zatim se gradi archive URL za primary filing HTML.

Pre upisa se proveravaju HTTP rezultat, content bytes, očekivani filing identitet
i metadata. Preprocess faza kasnije ponovo proverava usklađenost filename godine,
form-a, ticker-a, CIK-a i perioda. Dvostruka validacija je namerna: acquisition
greška ne sme da se pojavi tek kao neobjašnjiv retrieval pad.

| Operacija | Menja raw? | Menja derived? | Tipičan failure |
|---|---:|---:|---|
| `fetch_data` | kreira novi snapshot | ne | network/SEC/user-agent/form |
| `process-existing` | ne | kreira blocks | metadata/HTML/parser |
| chunk generation | ne | kreira chunks | invalid config/section |
| embedding | ne | kreira NPZ + manifest | model/shape/alignment |
| Qdrant import | ne | kreira collection | point/hash/count mismatch |

Raw immutability nije samo organizaciona konvencija. `data/raw/` je eksperimentalni
kontrolni uzorak. Ako se parser poboljša, isti bytes mogu ponovo proći kroz novi
parser; ako se preuzme novi filing, to je nova verzija i nova evaluacija.

## 5A. DOM parser: od HTML stabla do blokova

Parser radi kao ordered traversal, a ne kao globalno sabiranje teksta. U svakoj
tački traversal-a čuva se section state. Heading signal može doći iz HTML taga,
bold/styled pasusa ili standardnog SEC Item izraza. Block extractor odlučuje da li
je node heading, paragraph, list item ili table, a zatim emituje jedan strukturiran
zapis sa source locator-om.

### Zašto je redosled važan?

Ako se svi paragrafi prikupe nezavisno od tabela, tekst pre tabele može izgubiti
odnos prema tabelarnom naslovu. Ako se sadržaj stranice deduplikuje globalno,
ponovljeno zaglavlje može biti uklonjeno baš iz nastavka gde je potrebno za
tumačenje redova. Zato je cleanup konzervativan: uklanja samo elemente za koje
postoje jaki signali da nisu filing sadržaj.

### Inline XBRL granica

Inline XBRL ima dve suprotne opasnosti. Uklanjanje celog wrapper-a može ukloniti
vidljivi broj ili label; zadržavanje skrivenih XBRL čvorova može ubaciti tehnički
sadržaj koji korisnik nikada nije video. Pravilo je: `ix:hidden` se uklanja,
vidljivi wrapper se unwrap-uje, a vidljivi tekst ostaje. Ova odluka čuva i
čitljivost i izvorni redosled.

### Block contract

| Polje | Funkcija |
|---|---|
| `block_id` | stabilna korelacija sa chunk-om i citation resolution |
| `ordinal` | proverava document order |
| `content_type` | razlikuje narrative, list i table |
| `section`/`section_path` | scope unutar filing-a |
| `text` | retrieval/generation tekst |
| `source_tag`/`anchor` | audit prema DOM-u |
| `table_schema_version` | kompatibilnost logical-table parsera |
| filing metadata | company-level provenance |

Validacija odbija prazan tekst, nedostajuća polja, nekonzistentne ID-jeve i
ne-serijalizabilne objekte. Output se prvo piše u privremeni fajl i tek onda
promoviše, čime se sprečava da čitalac vidi poluobrađen JSONL.

## 5B. Tabele: fizička geometrija, semantika i fallback-i

Najveća parser lekcija bila je da fizički HTML layout nije isto što i logička
semantika. `rowspan` i `colspan` određuju geometriju, ali ne govore da li je red
header, subtotal, balance ili podatak. Zato table-schema-v2 prvo čuva fizičko
stanje, a tek zatim primenjuje heuristike i klasifikaciju.

### Redosled rekonstrukcije

```text
HTML table
→ raw cell extraction
→ span-aware rectangular grid
→ removal of only fully empty alignment lanes
→ header/data boundary detection
→ title/section/unit inference
→ row-role and table-kind classification
→ logical table + raw-cell mapping
```

Prazna unutrašnja ćelija se ne uklanja, jer bi se vrednost iz sledeće kolone
pomakla. Globalna jedinica `mixed` nije poraz; ona je signal da se po kolonama
moraju čuvati preciznije jedinice. Nejasna tabela se zadržava kao evidence uz
fallback marker, umesto da se tiho izbaci.

### Konkretni failure mode-ovi

| Failure | Pogrešna pretpostavka | Popravka |
|---|---|---|
| Bold `td` header postaje data | samo `th` je header | formatting + header heuristika |
| Beginning balance postaje header | prvi red je uvek header | prvi numerički podatkovni red |
| 2026/2027/2028 postaju kolone | svaki numerički red je header | četvorocifrena godina nije dovoljna |
| RSU naslov se gubi | neposredni tekst je generički | title candidate filtering |
| višeredni debt header ostaje u body | samo jedan header red | header paths i contiguous pre-data rows |
| mixed money/percent dobija percent | jedna globalna jedinica | `column_units` + `mixed` |

QA nije zasnovan samo na lepom Markdown-u. Proverava se da su svi non-empty raw
cell sadržaji mapirani, da nema standalone marker kolona, da je Markdown validan,
da su headers/rows pravougaoni i da source mapping ostaje dostupan.

## 6A. Chunking kao eksperiment, ne kao proizvoljan parametar

Chunk size je istovremeno retrieval granularity i generation context cost. Mali
chunk može preciznije pogoditi jednu tvrdnju, ali gubi prethodni naslov, jedinicu
ili uslov. Veliki chunk daje više konteksta, ali otežava ranking i povećava
prompt. Zbog toga je poređenje rađeno na istim ulaznim blokovima i sa istim
strukturnim proverenama.

| Strategija | Prednost | Mana | Odluka |
|---|---|---|---|
| Character 1.200/150 | jednostavna istorijska baseline | ne odgovara model tokenima | povučena |
| Fixed token | precizan nominalni overlap | često seče rečenice | kandidat, nije promovisan |
| Recursive 250 | više jedinica i granica | više chunk-ova | kandidat |
| Recursive 500 | bolje granice, manje chunk-ova | veći pojedinačni tekst | promovisan |
| Table row fragments | manji retrieval units | gubi globalni table context | odbačeno |
| Complete logical table | čuva redove/header/units | može preći embedding limit | promovisano |

U dokumentima se čuva i karakter-span i token-span. Karakter-span olakšava
mapiranje nazad na tekst, a token-span omogućava proveru stvarnog overlap-a i
modelskog budžeta. Section prefix ulazi u limit jer će biti prisutan u kasnijem
generation context-u.

Najveća semantička odluka jeste izuzetak za tabele. Ako bi se tabela delila po
500-token limitu, pitanje “kako se total odnosi prema redovima?” moglo bi zahtevati
više nezavisnih pogodaka. Jedan kompletan logical table chunk čuva taj odnos. U
embedding fazi veliki table input može biti skraćen zbog modelskog maksimuma, ali
source JSON i BM25 ostaju kompletni; to je svesna kombinacija, ne tvrdnja da
embedding vidi sve tokene.

## 7A. Embedding input, poravnanje i gubitak dugih tabela

Embedding nije samo `model.encode(chunk['text'])`. Za narativ je tekst direktan,
ali tabela se pretvara u deterministički searchable view sa company/ticker,
section, region, title, units, header lines i row/value statements. Structured
table podaci se paralelno čuvaju za prikaz i exact evidence.

Manifest v3 je zaštita od tihog poravnanja. Loader proverava:

1. da chunk file hash odgovara manifestu;
2. da je redosled input hash-eva isti;
3. da NPZ count odgovara chunk count-u;
4. da je shape `(n, 768)`;
5. da su vrednosti finite i normalizovane;
6. da model revision i prefix policy odgovaraju konfiguraciji.

Current eleven-company manifests beleže 322 table embedding input-a iznad
512-token modelskog limita, sa maksimumom pripremljenog table input-a 4.606
tokena, i nula skraćenih narativnih inputa. Stariji broj od 28 pripada ranijem
desetokompanijskom snapshot-u i ne sme se koristiti za aktuelni korpus. Ovo je
važna poznata slabost: dense vector može slabije predstavljati kraj veoma velike
tabele. BM25 i buduća row/column retrieval strategija su prirodni pravci, ali
nisu promovisani bez nove evaluacije.

## 8A. Retrieval: od globalnog skora do scope-aware dokaza

Dense i BM25 score nisu direktno uporedivi. Zato se ne sabiraju sirove vrednosti;
RRF koristi poziciju u listi. Ako je chunk visoko u dense i BM25 rezultatima, dobija
veći fused score. `rrf_k` kontroliše koliko se rank razlika ublažava. Promovisani
runtime koristi `rrf_k=100` i kandidat dubinu 50, dok istorijski corpus benchmark
beleži raniju RRF konfiguraciju; zato su rezultati označeni verzijom.

### Candidate recall naspram final recall

Ova razlika je presudna. Candidate recall pita: “da li je gold dokaz negde u
širem pretraženom pool-u?” Final recall pita: “da li je dokaz u stvarnom promptu
koji je model dobio?” U P0 eksperimetu candidate recall je bio 1,0, ali final
recall 0,5887. To znači da embedding i kandidat retrieval nisu bili prvi kvar;
evidence selection je izbacio dokaz.

### Balanced allocation algoritam

Za svaki atomic subquery i ticker pravi se nezavisan pool. Stable ID merge čuva
više provenance zapisa umesto da duplicira chunk. Selector zatim radi u rundama:

```text
1. uzmi do dva dostupna chunk-a iz svake subquery lane;
2. ponavljaj po company/subquery lane dok postoji budžet;
3. popuni preostale slotove globalno po RRF + 0.01 bonusu;
4. dedupliciraj po stable chunk ID-u;
5. packuj kompletne chunk-ove pod generation token budget;
6. zabeleži eventualno neispunjenu kvotu u trace-u.
```

Algoritam ne garantuje da je svaki chunk jednako dobar. Garantuje samo policy
granice i fer priliku za svaku podržanu kompaniju. Ako jedna kompanija nema
dovoljno kandidata, druge ne smeju automatski pojesti ceo budžet bez zapisa o
neispunjenoj kvoti.

### Metrike po tipu pitanja

Saved dense summaries sadrže odvojene rezultate za `single_narrative`,
`single_table`, `two_chunk` i `three_chunk`. Na BGE v3, Recall@10 je 0,7107 za
single narrative, 0,7875 za single table, 0,6786 za two-chunk i 0,5402 za
three-chunk pitanja. Complete@10 je odgovarajuće 0,7107, 0,7875, 0,4429 i
0,1724. Ovo pokazuje da je “hit” lakši od kompletne multi-chunk pokrivenosti i
da multi-part evidence ostaje glavni retrieval izazov.

### Company variance

Na BGE dense v3 Recall@10 je zavisio od kompanije: Ford 0,9556, NVIDIA 0,7389,
Tesla 0,6833, Mobileye 0,6222, General Motors 0,7056, Alphabet 0,7389,
Qualcomm 0,4833, Aurora 0,6611 i Ouster 0,6667; tačne vrednosti po svim
kompanijama ostaju u saved summary artifact-u. Razlike ne treba interpretirati
kao kvalitet kompanije: odražavaju terminologiju, chunk distribution,
query/gold sastav i disclosure format.

## 8B. Scope resolver, query rewriting i failure analysis

Resolver prvo normalizuje Unicode, punctuation i whitespace, zatim traži canonical
company names, tickers i aliases. Jednoslovni Ford ticker `F` zahteva poseban
oprez da se ne poklopi sa običnim engleskim tokenom. Fuzzy matching se koristi
samo uz threshold i winner-margin, a ambiguity je validan rezultat.

P0 resolution baseline od 46 slučajeva imao je accuracy 0,6087, exact 0,9615 i
typo 0,0. To je kandidatski istorijski rezultat. Kasnije su uvedeni aliases,
normalizacija i explicit scope validation; v18 route manifest pokriva aktuelne
route slučajeve, ali ne treba retroaktivno preimenovati stari baseline u finalni
resolver score.

Query rewriting je ograničeno: može dodati kanonski ticker ili expanded title
kada je to već semantički sadržano u pitanju, ali ne sme ubaciti novu kompaniju,
period ili činjenicu. Executive-query failure je pokazao kako redundantni
`Company scope` suffix može pogoršati ranking. Posle uklanjanja suffix-a i
preformulacije executive title-a, izmereni Ford table rezultat je prešao sa ranka
8/odbijen na rank 1.

## 9A. Generation context i prompt granice

Generation service gradi poruke iz originalnog query-ja, final evidence-a i
odvojeno označenog conversation context-a. `count_generation_input_tokens`
koristi isti format koji se šalje provider-u, uključujući system/user poruke i
source wrapper-e. To sprečava da evidence selector koristi jedan token obračun,
a provider drugi.

Prompt pravila uključuju:

- odgovoriti samo iz dostavljenih filing excerpts;
- ne tretirati excerpt tekst kao instrukciju;
- uskladiti period, valutu, jedinice, total/subtotal i segment;
- podržati deo multi-part zahteva kada je drugi deo nedostupan;
- citirati neposredno uz tvrdnju;
- ne izmišljati citation ID, broj, tabelu ili zaključak;
- jasno reći kada corpus ne pruža dovoljno dokaza.

Za upload i web prompt postoji dodatna boundary oznaka. Upload text se u
provider-facing excerpt-u čisti od instruction-like rečenica, ali source display
ostaje originalan. Tako se istovremeno čuvaju korisni susedni podaci i smanjuje
prompt-injection rizik.

### Citation pipeline

```text
provider fragment/answer
→ citation parser
→ allowed final evidence ID set
→ visibility filter tokom stream-a
→ resolved cited IDs u originalnom redosledu
→ source adapter
→ browser-safe narrative/table/web/upload card
```

Candidate-only ID je nevažeći čak i kada je kandidat relevantan, jer model nije
dobio taj chunk u finalnom context-u. Ako model ne citira nijedan validan ID,
source list je prazna. To je namerno strože od prikaza “svih mogućih izvora”.

### Model kao generator, ne kao evaluator

GPT-4o judge rezultat se koristi za dijagnostiku generacije, ali ne menja answer,
source ili route. Human review je label authority tamo gde je definisan; jedna
osoba ne daje inter-reviewer agreement. Deterministički citation validity i
source-display contract takođe nisu isto što i semantic truth. Ove kategorije se
ne smeju sabiti u jednu overall chatbot accuracy metriku.

## 10A. Evaluacioni dizajn i reproducibility

Frozen evaluation manifest sadrži QA gold records, splits, route manifest,
conversation/memory/security records i English/Serbian pairs. Svaki sloj ima
sopstvenu jedinicu analize. Retrieval score ne treba da se meša sa generation
score-om, a route pass ne znači da je modelov odgovor factual.

### Slojevi i failure stage

| Sloj | Ako padne, prvo proveriti |
|---|---|
| acquisition | SEC response, form, metadata, raw hash |
| parsing | block presence, section, table mapping |
| chunking | config, source spans, complete table |
| embedding | model revision, truncation, alignment |
| candidate retrieval | dense/BM25 query, scope, top-k |
| selection | company quota, dedup, token budget |
| planner | task schema, ticker, dependencies |
| tool | trusted source/calculator contract |
| generation | prompt, context, provider, abstention |
| citations | ID parse/resolution/source adaptation |
| UI | SSE, schema, rendering, accessibility |

Holdout discipline je deo metodologije, ne samo release procedure. Prompt i
policy promene su prvo proveravane na development set-u, zatim na validation
gate-u; holdout se ne koristi za tuning. V19 freeze manifest zaključava code tree,
artifact hashes, prompt hashes i effective runtime.

### Regression test inventory

Test suite pokriva `tests/test_dom_processing.py`, `test_table_processing.py`,
`test_sec_table_fixtures.py`, `test_chunk_documents.py`, `test_embed_chunks.py`,
`test_scope_aware_retrieval.py`, `test_qdrant_retrieval.py`,
`test_generation.py`, `test_generation_quality.py`, `test_llm_router.py`,
`test_llm_router_memory.py`, `test_calculator.py`, `test_web_search.py`,
`test_conversations.py`, `test_postgres_conversations.py`,
`test_document_service.py`, `test_document_retrieval.py`, backend/API tests,
frontend `App.test.tsx`, language, observability, security i freeze tests.

V19 ledger beleži focused backend 151 passed, frontend 43 Vitest tests, lint,
TypeScript i build passed. Stariji full-suite handoff beleži 446 passed, 3
skipped, 267 subtests. Skips su external-service integration uslovi, ne tihi
pass rezultati.

## 11A. Od deterministic routing-a do typed planner-a

Početni dizajn je imao više mesta koja su mogla doneti odluku: deterministic
router, route LLM, retrieval planner i memory scope. Svaki je bio lokalno
razuman, ali globalna kompozicija nije bila stabilna. Jedan route enum nije mogao
izraziti “10-K CEO plus current price”, niti “Ouster filing plus upload source”.

Finalni `TaskPlan` je finite typed object. Sadrži `schema_version`, original query,
memory resolution i tasks. Task ima `task_id`, `kind`, ticker scope, internal
query, dependencies i samo dozvoljene tool-specific fields. Server ne izvršava
šta nije u planu i ne dopušta planneru da sam proširi plan.

### Plan validation stages

```text
provider JSON
→ narrow presentation normalization
→ typed schema validation
→ canonical ticker/scope validation
→ ownership and memory-ID validation
→ dependency DAG validation
→ trusted source and URL validation
→ task/tool budget validation
→ bounded execution
```

Uvedene narrow normalizacije rešavaju provider presentation drift, ali nisu
general-purpose repair. Ako provider vrati pogrešan URL, nepoznat memory ID,
nevalidan ticker ili cikličnu dependency, plan se odbija. Ovo povećava broj
clarification/error ishoda, ali smanjuje opasnost od implicitnog izvršavanja.

### Memory reference resolution

Planner može da pročita top-k owner-scoped memory candidate-e i da odabere
reference poput `preferred_company`. Ne sme iz memory text-a zaključiti CEO,
revenue ili price. Ako postoje “favorite CEO = X” i “preferred company = Y”, to
nisu automatski konfliktni company memories, jer su typed odnosi različiti.

Ova granica objašnjava zašto je memory context odvojen od evidence. Memory može
odgovoriti “koja je moja preferred company?”, ali pitanje “ko je njen CEO?” mora
ponovo ići u filing ili web evidence.

## 12A. Web bez gubitka provenance-a

Web alat nije generički browser. `TavilyWebSearchTool` dobija query, ticker scope,
trusted source keys i limit rezultata. `allowed_domains_for` iz source key-a
izvodi konkretne hostove. Planner ne može da kaže “pretraži bilo gde”.

### Source classes

| Key | Primer namene | Dodatna kontrola |
|---|---|---|
| `sec_edgar` | filings, XBRL, issuer metadata | SEC host registry |
| `issuer_official` | leadership/product/news | ticker-specific issuer domains |
| `vehicle_regulator` | recall/safety | NHTSA registry |
| `market_primary` | exchange quote/status | ticker-specific exchange |
| `market_secondary` | bounded quote corroboration | approved Robinhood path |
| `news_independent` | recent news | Reuters registry |

URL sanitizer zahteva HTTPS, odbija credentials, unsafe ports, localhost,
private/non-global IP adrese i ne prati redirect automatski. Response bytes,
excerpt length i HTML cleaning su bounded. Web answer nosi publisher, URL,
retrieved timestamp i, za quote, source-declared delay/market status.

Stari failure je bio web-disabled fallback u 10-K retrieval. To je semantički
opasno: stock price ne sme biti odgovor iz poslednjeg annual report-a samo zato
što provider nije konfigurisan. V19 routing path tu grešku tretira kao unavailable
web evidence i ne tvrdi da je filing dovoljan.

### Calculator evidence protocol

Calculator ima dve ulazne klase. Kod `direct_calculation`, svi operandi su u
upitu. Kod `evidence_calculation`, filing/upload retrieval prvo mora vratiti
cited operands, a tek onda server parsira plain decimal vrednosti, proverava
jedinice/period i izvršava Decimal expression. Model ne šalje proizvoljan Python
izraz.

False positives su bili jednako važni kao true positives. “Repeat Elon Musk ten
times”, broj slova u CEO imenu ili lista od deset stavki nisu aritmetika. Phase 10
regression beleži exactness 1,0 na 10 slučajeva; route manifest beleži zero
calculator false positives. Calculator i dalje zavisi od effective deployment
configuration, pa historical disabled runs ne smeju biti preimenovani u failure
računanja.

## 13A. Conversation context, upload ownership i deletion

PostgreSQL je canonical jer relational model prirodno čuva redosled, ownership,
source-use i deletion audit. Qdrant memory je derived semantic index; njegov
point-count ili similarity ne određuje da li poruka pripada korisniku.

### Short-term context algorithm

Context builder:

1. učitava server-selected conversation turns;
2. formira complete-turn grupe;
3. izuzima aktivni turn;
4. računa `o200k_base` token budget;
5. bira newest-first uz skip oversized turn-a;
6. vraća izabrane turnove u chronological order;
7. dodaje extractive summary ako ulazi u limit;
8. označava sve kao untrusted context.

Ovaj proces nije query rewrite u smislu menjanja korisničkog pitanja. Njegova
uloga je da planner razume “it”, “both”, “return to the first comparison” kada je
to bezbedno. Ako kontekst nije dovoljan, clarification je bolji od izmišljene
kompanije.

### Memory lifecycle

| Akcija | PostgreSQL | Qdrant | User control |
|---|---|---|---|
| explicit save | canonical item | upsert derived point | opt-in |
| chat-triggered save | validated stable preference | sync point | opt-in |
| edit | update source | replace point | Settings |
| delete one | delete/audit | delete point | Settings |
| delete all | cascade/audit | owner-filtered delete | Settings |
| reconciliation | enumerate canonical | rebuild owner index | operator job |

Threshold 0,55 i candidate-k 5 smanjuju slučajno ubacivanje memory text-a, ali
nisu dokaz semantic correctness. Zato se score, memory ID i selected reference
beleže u backend trace-u, ne prikazuju se kao filing source.

Uploads imaju dodatni chat scope. Dokument ne sme postati vidljiv drugom
korisniku ili drugom razgovoru zato što je semantic embedding sličan. `DocumentIndex`
primenjuje owner/chat filter, a service čuva bytes, extraction status, filename,
chunk ordinals i deletion lifecycle. PDF/text extraction je ograničen na
podržane MIME tipove i upload-size budget.

## 14A. Frontend kao security-aware adapter

Frontend arhitektura nije samo vizuelni sloj. API response modeli eksplicitno
razlikuju narrative source i structured table source. Narrative source prikazuje
tekst i filing/web metadata; table source prikazuje validated headers, rows i
units. Ako tabela ne može da zadovolji pravougaoni schema contract, UI je ne
rekonstruiše iz Markdown-a.

SSE lifecycle je:

```text
POST /api/chat/stream
→ delta fragments
→ sources exactly after resolution
→ done
```

`error` može zameniti normalan završetak. Status/thinking događaji su UI-only
aktivnosti i ne smeju se pretvoriti u provider error ili izložiti raw trace.
Assistant avatar se renderuje uz assistant poruke; user poruka nema AVA avatar.
Prvi non-empty fragment uklanja waiting bubble.

Light/dark CSS koristi semantic variables, fokus prstenove i responsive breakpoint
pravila. Wide tables imaju horizontal scrolling. `react-markdown` je podešen bez
unsanitized HTML-a. `src/frontend/src/i18n.ts` ima English/Serbian strings, ali
semantički source labels i backend ownership ostaju server-authoritative.

UI testovi proveravaju API normalizaciju, cited/uncited source display, table
rendering, empty-chat ponašanje, settings, sidebar, upload flow i language.
V19 phase ledger beleži 43 frontend tests passed, lint passed, TypeScript passed
i production build passed. Screenshot-i nisu kreirani u repozitorijumu; zato su
u izveštaju placeholders, a ne lažne reference na postojeće slike.

## 15A. Runtime lifecycle, readiness i observability

Startup u real mode-u učitava i validira svih jedanaest chunk fajlova i aligned
BGE NPZ artifacts, normalizuje embedding matrix, gradi in-memory BM25 i učitava
query embedder. Qdrant se ne rebuild-uje po request-u. Kada je Qdrant primary ili
shadow konfigurisan, alias i point count moraju odgovarati frozen manifestu.

Readiness i liveness imaju različite funkcije. Liveness govori da proces postoji;
readiness govori da su corpus, index i obavezne zavisnosti spremne. Stari bug je
bio test koji je čitao developer `.env` i nije bio hermetičan. Phase 1 je uveo
typed settings i test izolaciju, a v19 runtime freeze čuva effective configuration.

Request trace je oblik evidence-chain audit-a, ne analytics event za browser.
On može povezati query → resolver → subqueries → candidates → final IDs → prompt
usage → generated answer → citations → displayed sources. `OBSERVABILITY.md`
navodi p50/p95 stage, first-token i complete latency agregaciju, provider tokens,
error/cancellation rates i concurrency procenu.

Security controls imaju defense-in-depth karakter:

| Rizik | Kontrola | Ograničenje |
|---|---|---|
| prompt injection | untrusted labels + quarantine | model i dalje može pogrešiti |
| cross-owner leak | PostgreSQL predicates + Qdrant filters | claim configuration je critical |
| XSS | escaped Markdown + no raw HTML | external links napuštaju AVA |
| resource exhaustion | body/query/evidence/tool/time budgets | per-process limits nisu globalni |
| secret leakage | backend-only env + safe errors | logs zahtevaju access control |
| incomplete deletion | derived-first delete + reconciliation | nema distributed transaction |

## 16A. Hronologija razvojnih korekcija

Git istorija pokazuje da su važni napreci uglavnom došli iz failure diagnosis-a,
a ne iz dodavanja još jednog modela. `3899940` arhivirao je superseded planove;
`4dc016e`, `7f23dde`, `8d1c063` i `4f62c3d` podelili su generation, pipeline,
handlers i backend routes. To je smanjilo rizik da test importuje compatibility
facade dok runtime koristi drugu logiku.

`9059544` zamenio je Brave direktnim Tavily adapterom. `97df606` uveo je typed
plan validation. `af926d3` i `d7accb1` popravili su web instruction quarantine i
cancellation pre izvršenja. `6cef175` odbacio je repetition calculator routes,
`19eab34` vratio resolved product questions u filing path, a `a401492` rešio
possessive tickers.

U Phase 7 nizu, `c88488f` je uskladio explicit memory saves, `aeba7cb` dodao
owner memory control/provenance, `379e877` aktivirao bounded task execution,
`8b46695` normalizovao bezbedne provider planove, a `56d3ae6` popravio
unambiguous pronoun follow-ups. Poslednji commit za report cut je
`cce57af` (exclude raw HTML from language stats); v19 frozen artifact ostaje
vezan za source commit eksplicitno naveden u manifestu.

## 17A. Trade-off analiza kroz merljive posledice

Nijedna glavna odluka nije besplatna. Kompletne tabele čuvaju semantiku, ali su
duže. Balanced selection čuva fer company coverage, ali povećava retrieval i
generation context. BGE poboljšava ranking, ali je veći od MiniLM-a. Qdrant daje
persistent filtered index, ali zahteva service readiness, snapshot, alias i
parity procedure.

| Promena | Kvalitativna dobit | Merena cena ili rizik |
|---|---|---|
| MiniLM → BGE | Recall@10 0,5472 → 0,7072 | veća dimenzija/compute |
| BGE → hybrid RRF | Recall@10 do 0,8117 | BM25 index i fusion |
| hybrid → scope-aware | historical Recall@10 0,8411 | company/query policy |
| fixed selector → balanced | final recall 0,5887 → 1,0 na P0 | p50 ~530 ms → ~1.345 ms |
| row table fragments → complete table | multi-row context | table embedding truncation risk |
| free fallback → cited-only | source-display exactness 0,4286 → 1,0 | prazna source lista kada citation ne resolve |
| strict absence candidate → default | bolja abstention isolated | completeness, numeric i latency regresije |

Tabela ne predstavlja apples-to-apples jednu jedinu eksperimentalnu seriju;
rezultati pripadaju artifact-ima različitih faza i svaki je označen statusom.
To je razlog što report čuva historical baseline umesto da prikazuje samo
najbolji broj.

## 18A. Budući rad: kriterijum za promociju

Buduća optimizacija mora imati četiri elementa pre promocije:

1. precizno definisan failure koji rešava;
2. razvojni skup i nezavisan validation/holdout protokol;
3. before/after metrike po relevantnim kategorijama;
4. rollback artifact i updated freeze manifest.

Za reranking to znači da BGE-only rezultat ostaje baseline, candidate depth se
povećava, cross-encoder ocenjuje query/chunk parove, a porede se Recall@k, MRR,
table/narrative/multi-chunk i latency. Za multilingual retrieval treba proveriti
ne samo preveden odgovor već company resolution, gold chunk recall, numeric value
parity i citation-ID parity. Za memory treba meriti recall@k, precision, stale/
deleted retrieval zero-rate i owner isolation.

Ne treba uvoditi “better model” samo zato što je fluentniji. Ako model poveća
judge score, ali smanji citation recall, numerical correctness ili source exactness,
candidate ostaje neprihvaćen. Phase 10 strict-abstention experiment je upravo
primer takvog odbijanja.

## 19A. Zaključna sinteza istraživačkog doprinosa

AVA je rezultat koordinacije informacionog retrieval sistema i aplikacionog
security/ownership sistema. Najveći doprinos nije samo BGE embedding niti UI,
nego formalizovanje granica između: šta je izvor, šta je candidate, šta ulazi u
prompt, šta je memory, šta je alat, šta je citation i šta korisnik sme da vidi.

Na nivou podataka, raw HTML ostaje proverljiv, tables nisu žrtvovane radi
jednostavnosti, a chunk/embedding manifesti čuvaju reproducibility. Na nivou
retrieval-a, dense i lexical signal se kombinuju bez sabiranja neuporedivih
score-ova, a company scope se ne gubi u finalnom context-u. Na nivou generation-a,
model dobija strukturiran dokaz, a source panel prikazuje samo ono što je model
zaista citirao. Na nivou aplikacije, planner je bounded, tools su typed,
memory je owner-scoped i frontend nije trust boundary.

Ovaj dizajn ne uklanja sve greške. On omogućava da se greška lokalizuje, izmeri,
sačuva kao historical rezultat i ispravi bez skrivanja regresije. To je standard
koji je najvažniji za tehnički odbranjiv RAG sistem nad finansijskim dokumentima.

---

# Dodatak A — reprodukcija i čitanje artefakata

## A.1. Autoritet dokumenata

Za aktivno ponašanje redosled autoriteta je: v19 freeze manifest i njegovi
phase results; aktuelni source code i tests; `FINALIZATION.md`; zatim current
deployment/security/observability dokumentacija. `INTERNSHIP_REPORT.md` je
vredan istorijski tehnički zapis i čuva eksperimente, ali se stariji rezultati
ne preimenuju u v19 metrike.

`docs/archive/2026-09-pre-finalization/` sadrži deprecated plans, roadmap-e,
implementation prompts i ranije deployment/architecture kopije. Oni objašnjavaju
razvoj, ali nisu source of truth za frozen behavior. `CLEANING_AND_cHUNKING_REPORT_FINAL.md`
je poseban autoritativni izvor za parsing, tables, chunking i overlap, čak i kada
se stara terminologija u drugim dokumentima razlikuje.

## A.2. Minimalni pregled pre novog eksperimenta

| Provera | Pitanje |
|---|---|
| input hash | da li su isti raw/processed/chunk files? |
| schema | da li evaluator očekuje table-v2/chunk-v3? |
| model revision | da li su query/document prefixes isti? |
| index | da li su count, dimension, alias i point IDs isti? |
| labels | da li gold IDs pripadaju toj chunk konfiguraciji? |
| scope | da li se koristi global, company ili planner path? |
| generation | da li je evidence fixed, oracle ili real retrieval? |
| judge | da li je diagnostic ili human-authoritative? |
| runtime | da li su web, calculator, memory i uploads enabled? |
| status | da li je rezultat baseline, candidate, promoted ili frozen? |

Bez ove tabele lako je porediti Mobileye-only dense baseline sa corpus-wide
hybridom ili disabled-calculator end-to-end run sa enabled route manifestom.

## A.3. Reprodukcioni komande

```bash
# acquisition and processing
.venv/bin/python -m src.filings.fetch_data
.venv/bin/python -m src.filings.preprocess_filing mobileye

# current chunk and embedding path
.venv/bin/python -m src.chunking.chunk_documents mobileye --overwrite
.venv/bin/python -m src.embeddings.embed_chunks mobileye --device cpu

# audits
.venv/bin/python -m src.filings.audit_tables --strict
.venv/bin/python -m src.embeddings.audit_embeddings --strict

# backend regression suite
.venv/bin/python -m unittest discover -s tests -v

# application
./start_app.sh
```

Tačni datumi, commit-i, hashes, model revision, Qdrant versions, prompt hashes,
tool limits i effective settings za frozen v19 nisu implicitni u komandi; nalaze
se u `data/evaluation/finalization/v19/freeze_manifest.json`.

## A.4. Interpretacija glavnih rezultata

Recall nije correctness odgovora. Candidate recall nije final evidence recall.
Source-display exactness nije semantic entailment. LLM judge score nije reviewed
human truth. Latency sa jednim provider outlier-om nije SLO. Calculator exactness
na 10 slučajeva nije dokaz za proizvoljnu aritmetiku. Serbian citation parity
0,8 nije contradiction sa numerical parity 1,0; to su različite metrike.

Ovo razdvajanje je metodološki uslov za fer zaključak. AVA je dobro proverljiv
pipeline sa jasno dokumentovanim prednostima, ali ne treba ga predstavljati kao
univerzalno tačan finansijski analitički sistem.

---

# Dodatak B — detaljna interpretacija sistema

## B.1. Kako čitati AVA kao istraživački sistem

AVA nije jedan model, već kompozicija pet različitih vrsta stanja: immutable
filing state, derived retrieval state, request state, owner state i presentation
state. Filing state potiče iz SEC-a i mora ostati stabilan. Retrieval state čine
chunk-ovi, vektori, BM25 indeks i Qdrant payload. Request state obuhvata query,
plan, candidates, final evidence, tool records i answer. Owner state sadrži
conversation, memory i uploads. Presentation state obuhvata SSE status, UI
tema, otvorene source cards i lokalni transient prikaz.

Ova podela objašnjava nekoliko neintuitivnih pravila. Qdrant filing point nije
isto što i user memory point, iako su oba vektori. A citation ID nije isto što i
interni chunk ID: prvi je modelov validated reference, drugi je backend korelacioni
identitet. A conversation summary nije fact table; ona je kompresovan korisnički
kontekst koji može pomoći planneru, ali ne može override-ovati SEC evidence.

| Vrsta stanja | Vlasnik | Može se menjati tokom request-a? | Prikazuje se korisniku? |
|---|---|---:|---:|
| Raw filing | SEC snapshot | ne | link/provenance |
| Processed block/chunk | local release artifact | ne | indirektno kroz source |
| Filing vector | Qdrant/index | ne tokom chat request-a | ne direktno |
| Query/plan/trace | server request | da | samo safe status |
| Conversation | PostgreSQL owner | da | da, kroz sidebar |
| Long-term memory | PostgreSQL + Qdrant | samo uz policy | da, kroz Settings |
| Upload | owner/chat service | da | da, kao upload source |
| Model output | provider + server | da | da, posle filtering-a |

## B.2. Zašto je failure-first razvoj bio neophodan

Rani razvoj je pokazao da “answer quality” može sakriti više različitih kvarova.
Ako model kaže da ne zna podatak, uzrok može biti da je podatak stvarno odsutan,
da je tabela izgubljena u parseru, da je tabela prisutna ali embedding skraćen,
da je kandidat retrieval nije vratio, da je selector izbacio kandidat ili da je
model zanemario dokaz. Svaki slučaj ima drugačiji fix.

| Simptom | Mogući sloj | Pogrešan brzi fix | Ispravan dijagnostički redosled |
|---|---|---|---|
| model abstain-uje | retrieval/generation | promena system prompt-a | proveriti raw → block → chunk → candidate → final |
| pogrešna kompanija | resolver/planner | dodati još aliasa naslepo | proveriti exact/fuzzy/margin i scope |
| citation postoji ali source nije relevantan | generation/citations | prikazati sve retrieved izvore | proveriti allowed final ID intersection |
| tabela izgleda prazno | parser/frontend | parsirati Markdown u browseru | proveriti schema-v2 headers/rows/units |
| current answer koristi 10-K | routing | povećati top-k filings | proveriti freshness route i web availability |
| memory utiče na fact claim | context boundary | ukloniti svu memoriju | typed reference + evidence hierarchy |

Ovakav triage je razlog zbog kog report čuva candidate recall i final recall
odvojeno, kao i route manifest, generation-quality run i UI contract. Jedan
overall score bi sakrio najvažniji podatak: gde sistem prvi put prestaje da ima
dovoljno informacija.

## B.3. Kontrola promena i kompatibilnost schema

Schema version nije dokumentaciona dekoracija. `table_schema_version=2` označava
da table chunk sadrži logical headers/rows/units i raw mapping. `chunk-schema-v3`
označava trenutni chunk identity/provenance ugovor. Embedding manifest-v3 zna
kojim redom su chunk-ovi kodirani. Qdrant artifact version zna koji corpus snapshot
je importovan. Promena bilo kog od ova tri nivoa mora promeniti hash ili release
manifest.

| Promena | Posledica | Potreban postupak |
|---|---|---|
| parser header heuristic | promena block/chunk sadržaja | regenerate processed, chunks, embeddings |
| chunk size/overlap | novi chunk IDs i gold mapa | new config-specific evaluation |
| embedding revision | novi vectors i ranking | new manifests, index, retrieval run |
| RRF/candidate policy | promena final evidence | selection/e2e regression |
| prompt | promena claim/citation behavior | generation and citation run |
| planner schema | promena route/tool execution | route manifest and security run |
| migration | promena ownership/history | DB isolation and deletion tests |

Release validator odbija dirty worktree, post-freeze source changes, input hash
promene i Qdrant mismatch. To sprečava čestu grešku u RAG projektima: izmeriti
novi rezultat na novom corpus-u, a zatim ga prijaviti kao model improvement.

## B.4. Kvalitativna analiza snaga i slabosti

Najveća snaga je traceability. Za realni request može se rekonstruisati originalno
pitanje, resolver odluka, planner subqueries, candidates, selected evidence,
provider usage, generated citations i source cards. Druga snaga je očuvanje tabela;
finansijski odgovor nije sveden na nepovezane ćelije. Treća je backend ownership:
frontend ne može da tvrdi da mu pripada conversation ili memory.

Najveća slabost je složenost. `src/orchestration/executor.py`, raniji
`src/backend/pipeline.py` i `src/generation/rag.py` akumulirali su veliki broj
odgovornosti. Refactor je smanjio granice kroz handlers, service, provider,
planning i routes, ali compatibility facades i dalje povećavaju kognitivni teret.
Druga slabost su male task-specific evaluacije. V18 60-case route gate je jak
za pokrivene route obrasce, ali nije dokaz da planner rešava sve jezike i sve
follow-up oblike. Treća je provider zavisnost: current web i streaming ponašanje
ne mogu se u potpunosti zaključiti iz local unit testova.

## B.5. Šta je uspešno validirano, a šta nije

| Tvrdnja | Status | Dokaz |
|---|---|---|
| 11 filing snapshot-a postoji | frozen/passed | v19 manifest |
| 4.526 chunks i vectors su poravnati | frozen/passed | v19 manifest/Qdrant audit |
| BGE je bolji od MiniLM-a u saved dense benchmarku | measured | dense summaries |
| hybrid/scope-aware retrieval je bolji u historical corpus poređenju | measured historical | internship/evaluation records |
| balanced selector popravlja P0 final survival | measured small gate | ava_p0 artifacts |
| citations ne prihvataju unresolved IDs | deterministic passed | citation tests/source adapter |
| 60/60 route/tool cases prolazi | measured v18 gate | v18 summary |
| svaka generisana tvrdnja je semantički tačna | nije dokazano | judge/human coverage ograničena |
| web je uvek dostupan | nije dokazano | provider-dependent |
| genuine token streaming radi kroz svaki gateway | nije dokazano | current gateway buffered |
| image ingestion postoji | nije implementirano | Phase 6 skipped |

Ova matrica je važnija od marketinškog opisa. Frozen release može pouzdano
garantovati contract-e koje testovi pokrivaju, ali ne treba proširivati zaključak
na nepokrivene modele, nove filings, nove jezike ili neodobrene izvore.

## B.6. Završna metodološka napomena

Detaljan report mora istovremeno biti koristan ML inženjeru i dovoljno oprezan da
ne meša različite populacije. MobilEye-only retrieval rezultat odgovara na
pitanje o toj evaluaciji; v18 route result odgovara na pitanje o planneru; human
review govori o malom reviewed packet-u; v1 disabled-runtime result govori o
prethodnoj konfiguraciji. Nijedan od njih samostalno ne opisuje ceo v19 sistem.

Zato su u ovom izveštaju statusi ponavljani: **historical baseline**, **candidate
experiment**, **promoted implementation**, **frozen release**, **known
limitation** i **future proposal**. Ponavljanje nije redundantno; ono sprečava
da se razvojni rezultat slučajno pročita kao finalna garancija.
