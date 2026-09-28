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
- `data_noop` (`--round-trip`) i `data_font` (`--build`) czytają
  `data.win` z `CC_GAME_ROOT`; zachowują przypięty hash źródła.
- `CC_OUTPUT_ROOT`: istniejący katalog zewnętrzny, domyślnie systemowy temp.
  Rezerwacja katalogów i zapis są no-clobber; reparse/aliasy instalacji są zabronione.
- `CC_FONT_BUNDLE`: wymagany katalog dokładnie przypiętego bundle fontów.
  Hashe, rozmiary i zgodność `source` w raporcie nadal obowiązują. Historyczny
  raport może blokować relokację; nie aktualizujemy go ani jego hashy automatycznie.
- CSX: `CC_FONT_REQUEST` lub `CC_FONT_ATLAS_REQUEST` wskazuje plik żądania JSON.
  Schematy definiują `FontBuilderCore.Request` i `FontAtlasUmt.Run`.
  Host: `UndertaleModCli load <data.win> --scripts <skrypt.csx>`; bez `-o`.

Ścieżki C#/CSX muszą być pełnymi lokalnymi ścieżkami Windows, bez `..`, ADS,
UNC i dowiązań. Nie uruchamiaj writerów ani gry w ramach testów jednostkowych.

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
syntetyczne modele, nie zapisuje gry. Bez tych wejść należy raportować pominięcie.
Nie uruchamiaj `archive_previous.py`: to historyczna, jednorazowa migracja
artefaktów, a nie część testów ani zwykłego builda.

Sprawdzamy m.in. 18 polskich liter, brak normalizacji, UTF-8/CRLF, round-trip
INI/pikseli/ZIP, tokeny, no-clobber, przypięte hashe i odmowy niebezpiecznych ścieżek.
