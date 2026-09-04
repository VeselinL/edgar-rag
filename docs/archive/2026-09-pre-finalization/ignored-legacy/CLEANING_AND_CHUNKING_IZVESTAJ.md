> **Archived 4 September 2026.** This previously ignored local document is
> preserved as a read-only historical record, not a current plan or authority.
> See [`FINALIZATION.md`](../../../../FINALIZATION.md) for the sole remaining-work plan.
>
# Izveštaj o čišćenju i čankovanju SEC 10-K dokumenata

**Datum preseka:** 13. avgust 2026.
**Primarni pilot-dokument:** Mobileye Global Inc. (MBLY), 10-K za 2025. godinu
**Dodatni dokumenti za proveru opštosti:** Aurora Innovation (AUR) i Tesla (TSLA)

## Ažuriranje: table-v2/chunk-v3 izdanje

Odeljci koji slede čuvaju istorijski tok eksperimenata i objašnjenje izbora
recursive 500/32 konfiguracije. Dana 13. avgusta 2026. završen je korpusni
popravak SEC tabela za svih deset filing-a. Novi `table_schema_version: 2`
razdvaja nepromenljiv fizički dokaz (raw ćelije, span-ove, XPath i fingerprint)
od validirane logičke tabele (lane kolone, uloge redova, putanje zaglavlja,
jedinice, naslov/region/klasifikacija i veze nastavaka). `chunk_schema_version:
3` pravi tačno jedan čank po uključenoj logičkoj tabeli, dok navigacione tabele
ostaju u processed dokazu i ne proizvode čank.

Promovisano `table-v2-chunk-v3.20260813-r2` izdanje ima 11.440 blokova, 914
fizičkih fragmenata tabela, 889 logičkih tabela i 4.115 čankova: 3.238
narativnih i 877 tabelarnih. Dvanaest
navigacionih tabela je ispravno isključeno. Audit ima nula kolizija, nemapiranih
nepraznih ćelija, samostalnih marker-kolona, `unknown` tabela, nevažećeg
Markdown-a ili rupa u provenance-u. Svih 10.526 narativnih blokova i 3.238
narativnih čankova ostali su byte-identični prethodnom izdanju.

Za svih deset kompanija ponovo su generisani BGE-base v1.5 vektori: ukupno
4.115 normalizovanih `float32` vektora dimenzije 768, uz manifest šemu 3 i
potpunu proveru hash-a, redosleda i input teksta. Zabeleženo je 28 pregledanih
truncation slučajeva, svi tabelarni; nijedan narativni ulaz nije skraćen. MBLY
gold skup je prebačen u verzionisani JSON sa 60 pitanja i 102 evidence veze.
Novi ukupni Recall@10 je 0,6167, a MRR@10 0,4446; istorijski uporediv podskup od
34 pitanja ostao je na tačnom Recall@10 0,720588. Reranking još nije pokrenut.

## 1. Cilj i početno stanje

Cilj ove faze bio je da se SEC 10-K HTML pretvori u stabilan i proverljiv skup jedinica pogodnih za retrieval. Postupak je zato podeljen na dve odvojene faze: čišćenje i strukturnu ekstrakciju, a zatim čankovanje već očišćenih blokova. Čišćenje mora da sačuva redosled dokumenta, sekcije, naslove, pasuse, liste, tabele i izvorne metapodatke. Čankovanje zatim formira jedinice koje su dovoljno uske za preciznu pretragu, ali sadrže dovoljno konteksta da pronađeni dokaz može pravilno da se protumači.

Od početka je uvedena granica između sirovih i izvedenih podataka. Originalni SEC HTML ostaje neizmenjen u `data/raw/`, strukturirani blokovi se čuvaju u `data/processed/`, a čankovi i njihove statistike u `data/chunks/`. Na taj način svaka uočena greška može da se prati unazad od čanka, preko izvornog bloka, do konkretnog mesta u originalnom filing-u.

Početni pristup zasnovan na izravnavanju kompletnog dokumenta jednim `get_text()` ili `text_content()` pozivom odbačen je jer gubi granice pasusa, pripadnost sekcijama i odnose između ćelija tabela. Kao osnovni međuformat izabran je JSONL sa tipiziranim blokovima, gde jedan red predstavlja jedan naslov, pasus, stavku liste ili tabelu.

## 2. Čišćenje DOM-a i očuvanje strukture

Prva funkcionalna verzija uvedena je kao konzervativno čišćenje HTML DOM-a. Uklanjaju se `script`, `style`, `noscript`, slike, SVG sadržaj, HTML `head`, eksplicitno sakriveni elementi i `ix:hidden`. Vidljivi Inline XBRL omotači se uklanjaju bez uklanjanja njihovog teksta. Normalizuju se HTML entiteti, nedeljivi razmaci, meki prelomi reči, zero-width znakovi i višestruki razmaci.

Ponavljajući naziv kompanije, broj strane ili sadržaj dokumenta uklanja se samo kada postoje dovoljno jaki strukturni signali da je reč o page furniture sadržaju. Nije primenjeno globalno uklanjanje duplikata, jer ista rečenica može legitimno da se pojavi u više napomena, a ponovljeno zaglavlje tabele može biti neophodno za razumevanje nastavka.

Očišćen DOM obilazi se redosledom pojavljivanja elemenata. Ekstrakcija počinje od stvarnog `Item 1`, čime se preskaču cover-page tabele i kopije stavki iz sadržaja. Prepoznaju se standardni HTML naslovi, ali i kratki podebljani ili stilizovani pasusi koji u SEC dokumentima imaju funkciju podnaslova. Emit-once obilazak obezbeđuje da se tabela emituje kao jedna semantička celina i da se njene ćelije zatim ne ponove kao zasebni pasusi.

Svaki blok dobija deterministički `block_id`, redni broj, identitet kompanije i filing-a, `section`, `section_path`, tip sadržaja, izvorni HTML tag, anchor i SEC URL. Pre upisa se proveravaju obavezna polja, neprazan tekst, sekvencijalni indeksi, deterministički ID-jevi i JSON serijalizacija. Upis je atomski, a postojeći rezultat se ne zamenjuje bez eksplicitnog `overwrite` zahteva.

## 3. Problemi sa tabelama i uvedene popravke

Najveće greške nisu bile u narativnom tekstu, već u tabelama. Idealizovani testovi sa `<th>` ćelijama prolazili su, dok stvarni SEC HTML često predstavlja zaglavlja podebljanim `<td>` ćelijama, koristi prazne kolone za poravnanje i odvaja `$` i `%` od numeričke vrednosti. Ručno poređenje MBLY JSONL izlaza sa originalnim HTML-om otkrilo je sledeće slučajeve:

1. `Name | Age | Position` je tretiran kao podatak jer je koristio podebljane `<td>` ćelije.
2. `Balance as of December 31, 2022` je pogrešno ušao u zaglavlje tabele promena kapitala, pa su brojevi iz prvog podatkovnog reda postali delovi naziva kolona.
3. Godine 2026, 2027. i 2028. u rasporedu lizing obaveza protumačene su kao zaglavlja, a naslov je preuzet iz generičkog imena kompanije.
4. Početno stanje `Outstanding as of...` u RSU tabeli završilo je u zaglavlju, dok je naslov `Restricted Stock Units` izgubljen.
5. Višeredna zaglavlja `Reported as` i `Amortized cost | Unrealized gain | ...` ostala su među podacima, a nastavak tabele izgubio je naslov `Debt Investments`.
6. Tabela koja kombinuje novčane iznose i procente dobila je jednu globalnu jedinicu `percent`.

Rešenje je izvedeno kroz opštiju rekonstrukciju, bez pravila vezanih samo za Mobileye. Svaka HTML tabela prvo se pretvara u span-aware pravougaonu mrežu. `rowspan` i `colspan` popunjavaju logičke pozicije; potpuno prazni redovi i kolone se uklanjaju, ali se unutrašnje prazne pozicije zadržavaju da se vrednosti ne bi pomerile u pogrešnu kolonu. Pored prikaznih `rows`, čuvaju se `raw_rows` i `raw_cells` sa koordinatama, span vrednostima, izvornim tagom, podebljanjem, poravnanjem i donjom ivicom ćelije.

Detekcija zaglavlja sada traži prvi verovatni podatkovni red. Red se smatra početkom podataka kada sadrži najmanje jednu numeričku vrednost koja nije samo četvorocifrena godina; svi prethodni uzastopni redovi čine višeredno zaglavlje. Naslov se traži neposredno pre tabele, uz odbacivanje naziva kompanije, page header-a i generičkog `Notes to Consolidated Financial Statements`. Prihvataju se i opisne rečenice koje završavaju izrazima kao što je `were as follows`.

Jedinice se čuvaju globalno i po kolonama. Globalno polje može biti, na primer, novčana jedinica, `percent` ili `mixed`, dok `column_units` čuva precizniju informaciju za svaku kolonu. Tabele se klasifikuju kao `data`, `text`, `navigation`, `list` ili `unknown`. Navigacione tabele se isključuju iz retrieval sadržaja, layout tabele sa bullet stavkama pretvaraju se u `list_item`, a nejasne tabele se zadržavaju umesto da se nepovratno odbace.

Za realne `<td>` slučajeve dodati su regresioni testovi. Trenutno je definisano 12 direktnih testova obrade tabela, pet testova čankovanja i tri testa prepoznavanja naslova. Testovi tabela pokrivaju span mrežu, navigaciju, bullet liste, nejasne tabele, naslov, jedinice, višeredna zaglavlja i svih šest prethodno uočenih MBLY grešaka.

## 4. Arhitekturna reorganizacija preprocessing-a

Početni `preprocess_filing.py` postao je prevelik jer je istovremeno učitavao filing, čistio DOM, prepoznavao sekcije, obrađivao tabele, pravio blokove i pisao rezultat. Funkcije su zato razdvojene prema odgovornosti, bez uvođenja dodatnih apstrakcija u sam tok obrade:

| Modul | Odgovornost |
|---|---|
| `filing_io.py` | Pronalaženje lokalnog 10-K dokumenta, parsiranje HTML-a i učitavanje filing metapodataka. |
| `dom_processing.py` | Normalizacija teksta, uklanjanje nevidljivog sadržaja i page furniture-a, prepoznavanje naslova i pomoćne DOM operacije. |
| `table_processing.py` | Span-aware rekonstrukcija, klasifikacija, zaglavlja, naslovi, jedinice i tekstualni prikaz tabela. |
| `block_extraction.py` | Redosled obilaska DOM-a, stanje sekcije i emitovanje tipiziranih blokova. |
| `preprocess_filing.py` | Orkestracija, validacija i atomski upis JSONL blokova. |

Ova podela je smanjila spregu između pravila. Promena detekcije zaglavlja tabele više ne zahteva menjanje filing I/O logike, a promena DOM čišćenja ne utiče na serijalizaciju. Istovremeno je zadržan jednostavan linearni tok:

```text
raw HTML → parse → DOM cleanup → ordered block extraction
         → block validation → processed JSONL
```

Prvobitni pilot izlazi za AUR, MBLY i TSLA imaju sledeću strukturu:

| Ticker | Ukupno blokova | Naslovi | Pasusi | List stavke | Data tabele | Text tabele | Navigacija |
|---|---:|---:|---:|---:|---:|---:|---:|
| AUR | 909 | 142 | 731 | 0 | 30 | 5 | 1 |
| MBLY | 1.258 | 240 | 858 | 107 | 43 | 9 | 1 |
| TSLA | 945 | 166 | 710 | 0 | 59 | 9 | 1 |

U međuvremenu su strukturirani blokovi generisani za svih deset kompanija iz odobrenog korpusa. MBLY ostaje primarni pilot za detaljnu kontrolu jer sadrži najveću raznovrsnost finansijskih, tekstualnih i layout tabela.

## 5. Prelazak sa broja znakova na broj tokena

Prvi benchmark čankovanja merio je veličinu u znakovima. Takva mera je jednostavna, ali nije stabilna u odnosu na stvarni kontekst modela: isti broj znakova može predstavljati veoma različit broj tokena, naročito kod tabela, brojeva, skraćenica i interpunkcije. Zbog toga su prethodne konfiguracije, uključujući baseline `1.200/150` znakova, povučene.

`chunk_documents.py` sada zahteva `length_function="tokens"`. Tokenizer i njegova tačna revizija zapisani su u `chunking-config.json`, pa se ista konfiguracija može ponoviti. Veličina, overlap, statistike i izvorni span-ovi mere se u tokenima. Za svaki narativni čank čuvaju se `source_token_start` i `source_token_end`, pored karakterskih pozicija koje služe za mapiranje nazad na izvorni tekst.

Narativ se grupiše samo unutar istog `section_path`. Prefiks sekcije uračunava se u budžet, pa kompletan narativni čank ne prelazi zadatu veličinu. Recursive strategija pokušava granice pasusa, redova, rečenica i reči pre krajnjeg presecanja. Fixed strategija koristi offsets tokenizatora i pravi delove tačne veličine sa predvidivim overlap-om. Obe strategije koriste isti ulaz i iste strukturne provere, što omogućava direktno poređenje.

Sekvencijalni `chunk_id` zavisi od konfiguracije. Prelazak sa znakovnih na 250-token i zatim na 500-token čankove promenio je broj i redosled čankova, pa stare gold oznake više nisu bile važeće. Evaluaciona pitanja zato imaju odvojene mape za 250 i 500 tokena, dok je stari skup sačuvan kao eksplicitno označena legacy znakovna verzija. Time se sprečava da promena čankovanja izgleda kao retrieval greška samo zato što evaluacija pokazuje na ID koji više ne postoji.

## 6. Token benchmark i izbor strategije

Benchmark je uporedio recursive i fixed strategije na MBLY i TSLA dokumentima, za 128, 192, 250 i 500 tokena. Sve uspešne konfiguracije ostvarile su 100% pokrivenost izvornih blokova, 100% tačnost sekcija i 100% prisustvo tabelarnog konteksta. Glavna razlikovna mera bila je granična tačnost: udeo narativnih čankova koji se završavaju rečeničnom interpunkcijom.

| Filing | Strategija | Veličina / overlap | Čankovi | Medijana tokena | Granična tačnost |
|---|---|---:|---:|---:|---:|
| MBLY | recursive | 250 / 32 | 778 | 164,5 | 76,4% |
| MBLY | fixed | 250 / 32 | 662 | 250 | 39,2% |
| MBLY | recursive | 500 / 32 | 464 | 253,5 | **91,7%** |
| MBLY | fixed | 500 / 32 | 426 | 258 | 62,6% |
| TSLA | recursive | 250 / 32 | 531 | 176 | 77,8% |
| TSLA | fixed | 250 / 32 | 474 | 250 | 45,3% |
| TSLA | recursive | 500 / 32 | 343 | 239 | **88,7%** |
| TSLA | fixed | 500 / 32 | 330 | 230 | 66,4% |

Recursive 500 je dao najbolji rezultat među testiranim konfiguracijama: najvišu graničnu tačnost na oba dokumenta, potpunu pokrivenost i znatno manje čankova od 250-token varijante. Fixed je bio nešto brži i dosledno ostvarivao zadati overlap, ali je mnogo češće prekidao rečenice. Dobit u brzini nije opravdala slabije semantičke granice.

Aktivna konfiguracija je zato:

```text
strategy: recursive
chunk_size: 500 tokena
chunk_overlap: 32 tokena
table_policy: jedna kompletna tabela po čanku
```

Istorijski MBLY izlaz iz vremena ovog benchmark-a davao je 464 čanka: 412
narativnih i 52 fizičko-tabelarna. Posle logičke kompozicije aktuelni rezultat
ima 462 čanka: istih 412 narativnih i 50 logičko-tabelarnih. Medijana je 248,
p95 492, maksimum 988 tokena, a granična tačnost ostaje 91,7%.

Recursive splitter ne garantuje stvarni overlap. Medijana izmerenog overlap-a na trenutnom MBLY izlazu je nula, jer su prirodne granice često pronađene pre nego što je preklapanje bilo potrebno. Zato se konfiguracioni i stvarni overlap čuvaju kao dve odvojene metrike.

## 7. Jedna kompletna tabela kao jedan čank

Prva politika delila je velike tabele na grupe redova koje staju u isti limit kao narativni tekst. U svakom fragmentu ponavljani su sekcija, naslov, jedinice i zaglavlje. To je davalo manje retrieval jedinice, ali je uklanjalo globalni pogled: poređenje udaljenih redova, agregacija i povezivanje ukupnih vrednosti sa sastavnim delovima zahtevali su više pogodaka.

Zato veličinski limit važi samo za narativ. Svaka uključena nenavigaciona
logička tabela proizvodi tačno jedan tabelarni čank, čak i kada prelazi 500
tokena. Jedna logička tabela može da objedini više susednih HTML fragmenata
vertikalno, horizontalno ili kao eksplicitne compound podtabele. Čank zadržava
sekciju, naslov, jedinice, zaglavlja, sve logičke redove i kompletan mapping ka
izvornim blokovima, HTML tabelama, anchor-ima, redovima i raw ćelijama.

Na aktuelnom MBLY dokumentu 53 fizička fragmenta čine 51 logičku tabelu: jedna
navigaciona ostaje samo u processed izlazu, a preostalih 50 daju tačno 50
tabelarnih čankova. Nijedan narativni limit ne preseca njihove redove.

## 8. Konačne odluke, ograničenja i naredne provere

| Odluka | Razlog |
|---|---|
| Sirovi SEC HTML ostaje neizmenjen | Omogućava ponovljivost i proveru prema originalu. |
| Čišćenje proizvodi tipizirane JSONL blokove | Struktura dokumenta ostaje dostupna pre čankovanja. |
| Preprocessing je podeljen po funkcionalnosti | DOM, tabele, blokovi i I/O mogu da se menjaju i testiraju nezavisno. |
| Tabele čuvaju span strukturu i unutrašnje prazne pozicije | Sprečava pomeranje finansijskih vrednosti između kolona. |
| Nejasne tabele se zadržavaju | Pogrešna klasifikacija ne postaje gubitak izvornog dokaza. |
| Dužina se meri tokenima | Budžet odgovara stvarnom ulazu u kasnije faze sistema. |
| Baseline je recursive 500/32 | Daje najbolju strukturnu granicu uz manje čankova i potpuno pokriće. |
| Jedna tabela je jedan kompletan čank | Čuva globalni kontekst tabele i pojednostavljuje kasniju upotrebu. |
| Gold ID mape su vezane za konfiguraciju | Promena veličine čanka menja sekvencijalne ID-jeve. |
| Konfiguracija i statistike se čuvaju uz rezultat | Omogućava reprodukciju i poređenje bez oslanjanja na sećanje. |

Preostala ograničenja su lokalizovana. Pipeline prepoznaje stvarne HTML
`<table>` elemente, ali ne zaključuje tabelu iz vizuelno poravnatih pasusa.
Naslov namerno ostaje `null` kada nema pouzdanog izvornog kandidata.
`page_start` i `page_end` još nisu popunjeni. Konzervativna lane podrška i
predugi embedding ulazi ostaju eksplicitna, pregledana upozorenja, a ne skriveni
fallback.

Sledeća provera čankovanja treba da koristi isti zamrznuti skup pitanja i odvojene gold mape za svaku konfiguraciju. Svaki promašaj prvo treba klasifikovati: nedostaje li dokaz u očišćenim blokovima, nalazi li se u pogrešnoj sekciji, da li je čank preširok ili preuzak, da li naslov i zaglavlja daju dovoljan kontekst i da li očekivani ID pripada aktuelnoj konfiguraciji. Tek nakon takve dijagnoze treba menjati veličinu, separatore, pravila tabela ili način grupisanja.

Aktivna konfiguracija i odgovarajući BGE-base manifest-v3 artefakti sada postoje
za svih deset filing-a, uključujući ponovo obrađenu Teslu. Persistent indeks,
retrieval servis, reranking, generisanje odgovora i UI ostaju naredne, odvojene
faze; nijedna nije dodata kao deo ovog popravka parsera.
> **Archived 4 September 2026.** This previously ignored local document is\n> preserved as a read-only historical record, not a current plan or authority.\n> See [\`FINALIZATION.md\`](../../../../FINALIZATION.md) for the sole remaining-work plan.\n>\n# Izveštaj o čišćenju i čankovanju SEC 10-K dokumenata
