# Development — wejścia i testy

Stos: **GameMaker/YYC (Windows x64)**, lossless adapter INI w Pythonie,
narzędzia fontów C#/CSX oparte na UndertaleModLib. Nie jest to projekt Unity.
Python: biblioteka standardowa; narzędzia wydania wymagają **Python 3.14+**
(API ZIP), Windows PowerShell 5.1 oraz `cmd.exe`. C#: **.NET SDK 10**.

## Wejścia poza repo

Nie publikujemy plików gry, korpusów `work/`, binariów UMT, katalogu `release/`
ani historycznych raportów wskazanych w `.gitignore`. Katalogi wejściowe muszą
istnieć; źródła są odczytywane, nigdy instalowane przez narzędzia development.
Raporty generowane lokalnie mogą zawierać ścieżki — nie dodawaj ich do commitu.

### Eksport przypiętego tłumaczenia

`python -B export_accepted_pl_001.py --export` przyjmuje:

| CLI | Zmienna środowiska | Domyślnie (względem cwd) |
| --- | --- | --- |
| `--source` | `CC_EXPORT_SOURCE` | `inputs/translations.ini` |
| `--snapshot` | `CC_EXPORT_SNAPSHOT` | `work/corpus/neutral-001.json` |
| `--accepted` | `CC_EXPORT_ACCEPTED` | `work/translation/accepted-pl-001.json` |
| `--output-root` | `CC_EXPORT_OUTPUT_ROOT` | `crypt-custodian-export` w systemowym temp |

CLI ma pierwszeństwo przed env. Wyjście musi być poza projektem i katalogiem
źródła, bez dowiązań; rodzic musi istnieć. Każdy eksport rezerwuje nowy
`release`, `release-002`, itd.; istniejące wyniki nie są nadpisywane.
Nie ma opcji zmieniającej przypięte hashe.

**Ograniczenie historycznego workflow:** snapshot, accepted i provenance mają
przypięte hashe oraz referencje zawierające ścieżki. Parametry nie przepisują
tych referencji. Loadery nadal wymagają `work/corpus`, `work/translation`
i katalogu warsztatu o nazwie `CrypyCustodian_PolishTranslation` (historyczna
pisownia). Samo skopiowanie JSON-ów do innego checkoutu nie zapewnia poprawnego
eksportu. Bez zgodnych wejść narzędzie odmawia; migracja korpusu/provenance
wymaga osobnego uzgodnienia. Nie edytuj danych ani hashy, by ominąć tę bramę.

### UMT i fonty

Zależności: komplet dystrybucji **UTMT CLI 0.9.2.0 Windows**, m.in.
UndertaleModLib, Underanalyzer, Magick.NET, SharpZipLib, Roslyn i Newtonsoft.Json.
Domyślne referencje projektów wskazują względne
`work/tools/UTMT_CLI_v0.9.2.0-Windows`; MSBuild przyjmuje alternatywny
`-p:ToolDir="$env:CC_UMT_DIR"` (wartość ma wskazywać istniejący katalog).
Skrypt `umt_font_atlas.csx` nadal wymaga SharpZipLib pod swoją względną ścieżką
`#r`; pozostałe referencje zapewnia host UMT. Nie pobieramy zależności automatycznie.

Inspekcja API dotyczyła źródeł UndertaleModTool w rewizji
`f43e12c445c37d50dc6244caa12ccab232983f3f`; nie dowodzi to identyczności
binarium z tym checkoutem. Zachowano istniejące bramy wersji i hashy DLL.

- `CC_GAME_ROOT`: wymagany rzeczywisty katalog instalacji, także gdy CSX czyta
  kopię `data.win` z innego miejsca. Jest niezależną chronioną lokalizacją.
- `data_noop` (`--round-trip`) nadal czyta `data.win` z `CC_GAME_ROOT`.
  `data_font` ma osobne `CC_DATA_SOURCE` (pełna ścieżka pliku oryginału, np.
  w backupie); bez tej zmiennej używa `CC_GAME_ROOT\data.win`. Błędne lub
  brakujące jawne wejście oznacza odmowę, nigdy fallback do instalacji.
  Oba narzędzia zachowują przypięty hash oryginału.
- `CC_OUTPUT_ROOT`: istniejący katalog zewnętrzny, domyślnie systemowy temp.
  Rezerwacja katalogów i zapis są no-clobber; reparse/aliasy instalacji są zabronione.
- `CC_FONT_BUNDLE`: wymagany katalog dokładnie przypiętego bundle fontów.
  Writer sprawdza sześć rozmiarów/SHA, zestaw plików, manifest, pokrycie,
  hashe 64 cropów i zgodność metryk z provenance. Tożsamość źródła to przypięty
  SHA i rozmiar, nie lokalizacja. Historyczne `report.source` pozostaje bez zmian
  w przypiętym raporcie; nie musi istnieć. Raport buildu zapisuje osobno rzeczywiste
  `source`, historyczne `bundleSource`, chroniony `gameRoot` i `outputRoot`.
- CSX: `CC_FONT_REQUEST` lub `CC_FONT_ATLAS_REQUEST` wskazuje plik żądania JSON.
  Schematy definiują `FontBuilderCore.Request` i `FontAtlasUmt.Run`.
  Host: `UndertaleModCli load <data.win> --scripts <skrypt.csx>`; bez `-o`.

Ścieżki C#/CSX muszą być pełnymi lokalnymi ścieżkami Windows, bez `..`, ADS,
UNC i dowiązań. Nie uruchamiaj writerów ani gry w ramach testów jednostkowych.

### Korekta optyczna małego ł — 2026-09-29

Na podstawie `tmp_screens/brzydkie-ł-2.png` zachowano bitmapę i kreskę,
zmieniając wyłącznie metryki małego `ł`. Skalowalna reguła:
`u=max(1,floor(EmSize/32+0.5))`, `Offset=poprzedni Offset+u`,
`Shift=Shift(l)+u`. Advance nie zależy od prawego skraju alfy ani canvasu;
overhang jest dozwolony. `Ł`, istniejące `l/L`, pozostałe glify, kerning,
baseline, rasteryzacja i geometria kreski/korpusu nie zmieniły się.

| Font | ł Offset | ł Shift | Ł Offset | Ł Shift |
| --- | --- | --- | --- | --- |
| Nerko | -5 → -4 | 14 → 9 | -6 → -6 | 19 → 19 |
| NerkoLarge | -9 → -7 | 30 → 23 | -9 → -9 | 52 → 52 |
| NerkoLarge2 | -8 → -6 | 26 → 20 | -10 → -10 | 46 → 46 |
| NerkoSmall | -5 → -4 | 12 → 7 | -6 → -6 | 16 → 16 |

Preview stdlib: `tests/font_atlas/preview_spacing.py`. Rzeczywiste bitmapy
z bundle i pełnego donor dumpu; incoming kerning, Offset i niezależny Shift,
bez systemowych fontów/rasteryzatora i bez klipowania overhangu do advance.
Wiersze A–F: obecny, bazowy Shift, bazowy+u, sam Offset+u,
Offset+u/bazowy Shift, **rekomendowany F: Offset+u/Shift(l)+u**.
Kolumny: `Obudził/aś się!`, `ił/`, `lł`, `łl`, `ł/a` dla wszystkich 4 fontów.
Podgląd nie jest testem renderowania GameMaker ani akceptacją użytkownika.

Artefakty poza repo pod `C:\Users\mdani\AppData\Local\Temp\opencode`:

- `cc-spacing-preview-libcu7zp/index.html`, 4 `Nerko*.png`, `metrics.json`;
- `cc-spacing-verified-lolo8ojv`: logi **233/233** syntetycznych i **297/297**
  z pełnym dumpem (64 kompozycje porównane z dotychczasowym bundle);
- `cc-spacing-verified-lolo8ojv/spacing-readback.json`: 4 atlasy identyczne
  bajtowo, 56 niestroke i 4 Ł bez zmian, wszystkie oryginalne modele glifów
  i baza/kreska bez zmian; zmieniają się tylko Offset/Shift/shiftDelta czterech ł;
- nowy bundle: `cc-spacing-verified-lolo8ojv/cc-font-atlas-007/release/atlas-001`;
  obok release jest `readback-verification.json` (ponowny odczyt, SHA, Unicode,
  coverage 18/18, 64 cropy, guttery, brak kolizji prostokątów atlasu).

Aktualne piny writera (RGBA pozostają jak w historycznej tabeli poniżej):

| Plik | Bajty | SHA-256 |
| --- | ---: | --- |
| manifest.json | 15394 | `519f8ab82cf08ca88aea3b52cacbf5d9a804e0ea9be80dd5fde63401f02bbad0` |
| report.json | 170011 | `02b2b719b0e11112156663837ed4776cdfd9d22c85fe57fed968ba4a4617d726` |

Do poleceń writera poniżej należy podać **nowy** `CC_FONT_BUNDLE`.
Testy regresji pełnego dumpu można odtworzyć dodając do runnera atlasu:
`-- --donor-dump <dump> --baseline-bundle <poprzedni-bundle>`.
Testy renderera: `python -B -m unittest discover -s tests/font_atlas -p 'test_*.py'`.
Preview: `python -B tests/font_atlas/preview_spacing.py --bundle <poprzedni-bundle>
--donor-dump <dump> --output-root <istniejący-temp-poza-grą>`; każda próba
rezerwuje nowy katalog bez nadpisywania. Nie używać nowego bundle jako baseline
porównania stary→nowy (skrypt pokazuje zmiany względem podanego wejścia).

#### Build i lokalna instalacja korekty małego ł

Wykonano po zgodzie użytkownika: dwa niezależne buildy z oryginalnego backupu,
readback UMT, porównanie bajtowe i instalację **tylko `data.win`**.
Wynik: **183021330 bajtów**, SHA-256
`edffc0eb9aea564d879b5719187465aef84a566e215b19abcdf93445b19a9edd`.
Oba kandydaty i zainstalowany plik są identyczne bajtowo.

- Bieżący writer **118/118**, atlas **233/233**, no-op/snapshot **64/64**,
  renderer PNG **4/4**, adaptacja instalacji **10/10**.
- Pełny readback: 30 chunków, 29 aliasów kolekcji, 18/18 liter w każdym
  foncie, zgodne metryki wszystkich 64 dodatków; 645 istniejących glifów
  i 142 starych tekstur zachowane. Inverse/expected/protected diff = 0.
- Instalacja przy zamkniętej grze: mutex, zweryfikowany backup no-clobber,
  staging na woluminie gry, atomowy Replace bez delete+copy fallbacku,
  SHA przed/po; INI trzymany tylko do odczytu. Staging został usunięty.
- `translations.ini`, EXE, oryginalny backup i istniejący ZIP zachowały
  rozmiary i SHA. Nie zmieniono danych tłumaczeń ani pinów paczki.
- **Nie uruchomiono gry**: odbiór wizualny `Obudził/aś się!` i par `ił/`,
  `lł`, `łl`, `ł/a` nadal wymaga testu użytkownika w runtime.

Dowody pod `C:\Users\mdani\AppData\Local\Temp\opencode`:

- `cc-spacing-build-20260929-02/execution.json`, `offline-gates.json`,
  `installation-selftests.json`, `final-report.json` i logi testów;
- dwa wyniki `cc-spacing-build-20260929-02/cc-data-font-00{1,2}/release`;
- backup poprzednio zainstalowanego pliku:
  `cc-spacing-active-backup-20260929-01/data.win`, SHA-256
  `f70167daa7c0911d6df2a574bc7de4bb7f886ba647df0c8020324114950e7349`;
  obok manifest ze znacznikiem UTC, sumą kontrolną i potwierdzeniem instalacji.

Pierwszy run `cc-spacing-build-20260929-01` został przerwany limitem 120 s
terminala podczas drugiego buildu; nie zmienił gry. Częściowych artefaktów
nie nadpisano. Run `-02` wykonano od początku z dłuższym limitem.

Kopia i skrypty są w **Temp**, nie są trwałym archiwum. Nie usuwać ich przed
odbiorem korekty. Odtworzenie poprzedniego pliku wymaga osobnej, jawnej decyzji
oraz zamkniętej gry; tylko dla tej instalacji dostępne jest:

```powershell
powershell.exe -NoProfile -File 'C:\Users\mdani\AppData\Local\Temp\opencode\cc-spacing-build-20260929-02\install-data-only.ps1' -Mode Rollback
```

Rollback realnej gry nie był wykonywany; test cofania odbył się na fixture.
**Nie zbudowano paczki.** Jej obecny instalator/deinstalator ma stare piny
i nie obsługuje nowego lokalnego `data.win`; nie używać go do cofania tej
korekty ani nie aktualizować pinów wydania bez osobnego uzgodnienia.

### Historyczny writer po korekcie advance ł/Ł — 2026-09-29

Adaptacja obejmuje wyłącznie `data_font_core.cs`, `tools/data_font/Program.cs`,
`tests/data_font/Program.cs` i ten dokument. Zastępuje historyczny kontrakt
stałych wejść z `DATA_FONT.md`; nie zmienia mutatora, STRG, serializera ani
allowlist pełnego grafu. Istniejące wcześniej zmiany `font_atlas_core.cs`
i jego testów pozostawiono nienaruszone.

Wykorzystano **już wygenerowany** bundle:
`C:\Users\mdani\AppData\Local\Temp\opencode\cc-lstroke-offline-20260929-01\cc-font-atlas-007\release\atlas-001`.
Obok `release/` istnieją `readback-verification.json` oraz
`advance-regression-readback.json`. Ponowny odczyt i hashowanie rzeczywistych
plików przez testy writera i `--verify-inputs` potwierdziły nowe piny:

| Plik | Bajty | SHA-256 |
| --- | ---: | --- |
| manifest.json | 15396 | `f33f521317bec3aab6c19714991cd1c209fcc88446b1fce4d1908bcf1dac81e4` |
| report.json | 170013 | `eee173df26a6066d4f3c723dd2e094bc939476a3677a46832235db16622de0e7` |
| Nerko.rgba | 184320 | `6a3ad0d72f164872b4b6607cce3667c9ba9b967074a61e1f634d72d95c411527` |
| NerkoLarge.rgba | 499712 | `0ffdafc87619c7ab2bc673e44021e1e8335a2339eb725f23589606cfa0d00ac5` |
| NerkoLarge2.rgba | 446464 | `8d0d703cb1403a3d3b8a96e7ea098d0001828a555546d875fd8630df0ba83592` |
| NerkoSmall.rgba | 159744 | `bbeafeac5dcfd6d65c078af9d632486a58eee4e1c80465a13feb9e90f7113e34` |

Nie ma auto-pinowania ani flagi pomijającej integralność. Piny RGBA są nadal
takie same, bo poprawka dotyczy advance, nie bitmap. Receptura pozostaje
`existing-pixels/v3`, zgodnie z rzeczywistym, przypiętym raportem; kontrakt nie
wymaga zmiany wersji receptury. Nie przepisywano istniejącego bundle ani dowodów.
Regresja ośmiu metryk ł/Ł sprawdza `max(base.Shift, alphaRightExclusive + 1)`,
z uwzględnieniem alpha=1. Dla czterech fontów advance ł wynosi 14/30/26/12,
a Ł 19/52/46/16. Nie jest to zatwierdzenie wyglądu w grze.

#### Dokładny kontrakt CLI

```text
DataFont.dll --verify-inputs
DataFont.dll --build
DataFont.dll --compare <pierwszy-release> <drugi-release>
```

Ścieżki wyłącznie przez env (brak `--source`, `-o`, `--force`, `--skip-hash`):

| Env | Znaczenie |
| --- | --- |
| `CC_GAME_ROOT` | Wymagany istniejący katalog aktywnej instalacji, chroniony niezależnie od źródła. Nie musi zawierać oryginalnego `data.win`. |
| `CC_DATA_SOURCE` | Opcjonalna pełna ścieżka pliku źródłowego. Domyślnie `CC_GAME_ROOT\data.win`. Dowolna nazwa pliku, ale wyłącznie oryginalne bajty. |
| `CC_FONT_BUNDLE` | Wymagany istniejący katalog dokładnie powyższego bundle, dopuszczalna byte-identical kopia w innym miejscu. |
| `CC_OUTPUT_ROOT` | Istniejący katalog zewnętrzny, domyślnie systemowy temp. Musi być poza game root, katalogiem źródła i bundle, również po rozwiązaniu aliasów woluminu/8.3. |

SHA oryginału pozostaje
`15e2c8ef57f4c5f599589b5d15021281a11b73757be54d0bbf739d57dd280b99`,
rozmiar **182801522**. `--verify-inputs` sprawdza wejścia/ścieżki i drukuje JSON,
bez parsowania gry, rezerwacji katalogu ani zapisu. Nie zastępuje bram buildu.
Kod 0 oznacza sukces; kod 2 odmowę/błąd.

`--build` rezerwuje świeży `CC_OUTPUT_ROOT\cc-data-font-NNN\release`, drukuje
`RELEASE <ścieżka>` i wykonuje jeden Write. `CreateDirectoryW`, `CreateNew`,
`FileShare.None`, flush i readback pozostają obowiązkowe. Ochrona wszystkich
trzech lokalizacji działa przed rezerwacją i przy **każdym** zapisie pliku.
Źródło otwierane tylko do odczytu, SHA sprawdzany także po buildzie.
Niepełne wyniki pozostają na dysku; brak cleanupu i nadpisywania. Nie jest to
sandbox przeciw współbieżnym zmianom ścieżek (TOCTOU).

Status `font-candidate-verified` wymaga nadal natychmiastowego readbacku UMT,
pełnych diffów/inverse, STRG, TXTR, TGIN, pikseli i 18/18 polskich liter.
Deterministyczność całego `data.win` wymaga dwóch osobnych udanych buildów,
a potem `--compare`: różne katalogi pod tym samym output root, raporty sukcesu,
zgodne bieżące piny bundle, SHA/rozmiary rzeczywistych outputów i porównanie
bajtowe. Stare buildy z atlasem 011 nie przechodzą nowej bramy pinów.
Dowód `determinism.json` zapisywany no-clobber przy drugim wyniku.
Relokacja źródła zmienia provenance, nie plan/piksele; identyczność przyszłych
pełnych buildów trzeba jednak wykazać, nie zakładać.

Przykład konfiguracji i wykonanej walidacji (PowerShell, katalog projektu):

```powershell
$env:CC_GAME_ROOT = 'F:\SteamLibrary\steamapps\common\Crypt Custodian'
$env:CC_DATA_SOURCE = 'C:\Users\mdani\AppData\Local\Temp\opencode\CryptCustodian-original-backup-001\data.win'
$env:CC_FONT_BUNDLE = 'C:\Users\mdani\AppData\Local\Temp\opencode\cc-lstroke-offline-20260929-01\cc-font-atlas-007\release\atlas-001'
$env:CC_OUTPUT_ROOT = 'C:\Users\mdani\AppData\Local\Temp\opencode'
$artifacts = Join-Path $env:CC_OUTPUT_ROOT 'cc-data-font-adapt-build-001'
# Przed kompilacją sprawdź istnienie rodzica $artifacts i katalogów wejściowych.
dotnet run --project tests/data_font/FontWriter.Tests.csproj --artifacts-path $artifacts --no-launch-profile --verbosity minimal
$writer = Join-Path $artifacts 'bin\DataFont\debug\DataFont.dll'
dotnet $writer --verify-inputs
```

Przyszły build, **niewykonany w tej adaptacji**, bez instalacji:

```powershell
dotnet $writer --build
# Tylko po kodzie 0 i font-candidate-verified: drugi osobny proces.
dotnet $writer --build
# Podaj dwa rzeczywiste katalogi RELEASE wypisane przez powyższe procesy:
dotnet $writer --compare '<pierwszy-release>' '<drugi-release>'
```

#### Weryfikacja adaptacji

- .NET SDK 10.0.401: kompilacja hosta i testów bez ostrzeżeń/błędów.
- Writer: **118/118** (syntetyczne modele i I/O w temp oraz read-only pinned
  bundle; bez odczytu/parsera prawdziwego `data.win` w testach). Raport:
  `C:\Users\mdani\AppData\Local\Temp\opencode\cc-data-font-tests-008\release\tests.json`.
- Atlas: **227/227**, w tym ł/Ł, alpha=1, UTF-8, deterministyczne piksele,
  bounded Bz2Qoi i kompilacja CSX bez wykonania.
- No-op/snapshot: **64/64**, bez parsowania gry i serializera.
- `--verify-inputs`: **kod 0 / inputs-verified**, rzeczywisty backup sprawdzony
  SHA/rozmiarem; sześć plików bundle i 64 cropy zweryfikowane ponownie.
  To odczyt bajtów backupu, nie test round-trip gry.
- Nie wykonano `--build`, `--compare`, instalacji, testów UI/runtime ani
  nowego pełnego round-tripu/determinizmu `data.win`. Brak nowego output SHA.
  Nie zmieniono `translations.ini`, danych tłumaczeń, release ZIP ani pinów
  narzędzi wydania; nowy payload nie jest jeszcze zatwierdzonym wydaniem.
- `git diff --check`: bez błędów whitespace. Bez commit/push.

Artefakty kompilacji: `cc-data-font-adapt-build-001`, `cc-atlas-adapt-tests-001`
i `cc-noop-adapt-tests-001` pod powyższym output root. Testy atlasu i no-op
uruchomiono analogicznym `dotnet run --project tests/font_atlas/FontAtlas.Tests.csproj`
oraz `tests/data_noop/Noop.Tests.csproj`, z osobnymi `--artifacts-path`,
`--no-launch-profile --verbosity minimal`.

### Budowanie paczki

`tools/release/verify_sources.py` i `build_release.py` wymagają:

- `--data` / `CC_RELEASE_DATA`: gotowy, offline, przypięty payload `data.win`;
- `--translations` / `CC_RELEASE_TRANSLATIONS`: gotowy, przypięty payload INI.

Nie korzystaj z aktualnie zainstalowanych plików jako payloadów. Obie sumy
kontrolne są stałe w `verify_sources.py`; brak wejść oznacza odmowę.
Builder wymaga `--candidate` albo `--build`. `--output-root` /
`CC_RELEASE_OUTPUT_ROOT` wybiera istniejący katalog wyjściowy (domyślnie temp).
`CC_RELEASE_TEMP` wybiera istniejący katalog dla testów i drugiego ZIP-a
(domyślnie systemowy temp). `--candidate` tworzy losowy nowy katalog;
`--build` tworzy paczkę o stałej nazwie i ZIP, nigdy ich nie nadpisuje.
Kolejność plików, timestamp ZIP, tryby plików i kompresja pozostają ustalone;
drugi ZIP jest porównywany bajtowo w tym samym środowisku Python/zlib.
Nie gwarantujemy identycznej kompresji między różnymi wersjami zlib.

## Testy bez zapisu do gry, lokalnego work/ ani release/

Z katalogu projektu:

```powershell
python -B -m unittest discover -s . -p 'test_*.py'
python -B -m unittest discover -s tools/release -p 'test_*.py'
```

Fixture'y Python powstają w systemowym temp. Integracja eksportera jest
domyślnie pomijana; wymaga `CC_EXPORT_INTEGRATION=1` i zgodnych wejść z tabeli.
Testy paczki wymagają lokalnego `release/`; historyczne porównanie wymaga też
poprzedniej rewizji. Nie podawaj `--preflight` podczas zwykłych testów.
Launcher: `python -B tools/release/test_launchers.py <katalog-paczki>` lub
`CC_RELEASE_PACKAGE`; odpala wyłącznie nieszkodliwe sondy cmd/PS w temp.

Przykład izolacji artefaktów .NET (PowerShell, z katalogu projektu):

```powershell
$artifacts = Join-Path ([IO.Path]::GetTempPath()) ('ccpl-tests-' + [guid]::NewGuid().ToString('N'))
dotnet run --project tests/data_noop/Noop.Tests.csproj --artifacts-path $artifacts
dotnet run --project tests/font_builder/FontBuilder.Tests.csproj --artifacts-path $artifacts -- (Join-Path $PWD 'umt_font_builder.csx')
dotnet run --project tests/font_atlas/FontAtlas.Tests.csproj --artifacts-path $artifacts
```

Te testy używają syntetycznych danych i tylko kompilują CSX (bez wykonania).
`tests/data_font/FontWriter.Tests.csproj` wymaga dodatkowo `CC_GAME_ROOT`,
`CC_FONT_BUNDLE` i zewnętrznego `CC_OUTPUT_ROOT`; testuje pinned bundle oraz
syntetyczne modele, nie czyta ani nie zapisuje gry. Opcjonalne `CC_DATA_SOURCE`
wybiera źródło jak powyżej; w tych testach musi istnieć tylko jego katalog
(prawdziwe bajty źródła czyta dopiero osobne `--verify-inputs` lub `--build`).
Bez wymaganych wejść należy raportować pominięcie.
Nie uruchamiaj `archive_previous.py`: to historyczna, jednorazowa migracja
artefaktów, a nie część testów ani zwykłego builda.

Sprawdzamy m.in. 18 polskich liter, brak normalizacji, UTF-8/CRLF, round-trip
INI/pikseli/ZIP, tokeny, no-clobber, przypięte hashe i odmowy niebezpiecznych ścieżek.
