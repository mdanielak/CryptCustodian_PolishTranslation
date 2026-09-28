# Crypt Custodian — polskie tłumaczenie

**Status: publiczna beta `1.0-beta`.**
Repozytorium: <https://github.com/mdanielak/CryptCustodian_PolishTranslation>

Nieoficjalne spolszczenie gry **Crypt Custodian** (GameMaker, Windows/Steam,
AppID `2394650`). Projekt obejmuje 1327 wpisów tekstu, interfejsu i dialogów,
cztery fonty Nerko oraz obsługę 18 polskich liter. Użytkownik pomyślnie
potwierdził działanie tekstów podczas testu runtime w grze.

## Instalacja

1. Pobierz paczkę z [GitHub Releases](https://github.com/mdanielak/CryptCustodian_PolishTranslation/releases).
2. Zamknij grę, rozpakuj ZIP i uruchom `instaluj.bat`.
3. Uruchom grę i wybierz język **English**.

Do usunięcia tłumaczenia użyj `odinstaluj.bat` z rozpakowanej paczki. Skrypt
przywraca pliki z kopii zapasowej utworzonej podczas instalacji.

## Ograniczenia

- 125 wpisów ma status *uncertain* i może wymagać korekty po zgłoszeniach graczy.
- Paczka jest przeznaczona dla konkretnej wersji gry sprawdzanej przed instalacją.
- Aktualizacja gry lub funkcja Steam **Verify integrity of game files** usuwa
  patch; po takiej operacji należy ponownie zainstalować zgodną wersję paczki.

## Development

Źródła zawierają skrypty Python do przygotowania i kontroli korpusu, adapter
lokalizacji, narzędzia C#/CSX do analizy i budowy fontów oraz testy jednostkowe.
Parametry wejściowe, zależności UMT i komendy testów opisuje
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Prywatne historyczne raporty,
pliki gry, korpusy robocze i artefakty wydania nie należą do publicznych źródeł.

## Prawa i zasady wydania

Pełne warunki zgody i informacje o prawach zawiera plik `LICENSE-NOTICE` w
paczce wydania. Zgoda właściciela praw została przekazana użytkownikowi;
użytkownik udostępnił jej treść na potrzeby projektu. Spolszczenie pozostaje
bezpłatną, nieoficjalną modyfikacją fanowską i wymaga legalnej kopii
Crypt Custodian. Nie rościmy sobie praw do oryginalnej gry ani jej zasobów.
