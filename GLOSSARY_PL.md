# Glosariusz polski — wersja robocza 1

Glosariusz dotyczy korpusu `english-001`. Obowiązuje w tłumaczeniu i korekcie,
chyba że kontekst konkretnego wpisu wymaga jawnie opisanej zmiany.

## Zasady nadrzędne

- Zachowywać wszystkie tokeny, tagi, `$`, `~`, parametry i ich kolejność 1:1.
- Nie tłumaczyć identyfikatorów wewnątrz tagów, np. nazw obiektów i sprite'ów.
- Zachowywać funkcję WERSALIKÓW, wielokropków i emfatycznych znaków zapytania.
- Dialog ma brzmieć naturalnie po polsku; nie kopiować angielskiego szyku.
- Opisy mechanik pisać bezosobowo. Do gracza zwracać się na „ty”.
- W krótkim UI preferować zwięzłość; docelowo nie przekraczać około 125% długości
  źródła bez sprawdzenia layoutu.
- Nie normalizować ani nie transliterować polskich znaków. Problem glifów jest
  osobną bramą techniczną.

## Nazwy i postacie

- Pip, Pebble, Grizz, Crouton, Dagoberg, Roy, Rusty, Skully, Mira, Kendra —
  zachować. W tekście odmieniać naturalnie: Pipa, Grizza, Miry, Kendry.
- Big Lisa — zachować jako imię własne.
- Little Guys (ID 1725) — roboczo **Mali Goście**; wymaga korekty kontekstowej.
- Wailer (ID 1101) — roboczo **Wyjec**.
- Bouncer (ID 1320) — **Wyrzucacz**.
- Kendra: styl wyniosły, ironiczny i złośliwie uprzejmy.
- Duchy poboczne: styl swobodny i potoczny; slang oddawać funkcjonalnie, bez kalk.

## Świat i fabuła

- The Palace / Palace — **Pałac**. Nie używać „zamek”.
- afterlife — **zaświaty**; `afterlife's janitor` — **dozorca zaświatów**.
- ghost — **duch**, ogólna nazwa mieszkańca zaświatów.
- spirit — zależnie od funkcji:
  - mechaniczna forma gracza: **duszek**;
  - pełnoprawna postać lub uwięziona istota: **duch**;
  - idiomy tłumaczyć znaczeniowo, np. `lift my spirits` → „poprawić mi humor”.
- Save Shrine — **Sanktuarium Zapisu**; Shrine — **Sanktuarium**.
- Broomerang / Broom-erang — **Miotlomerang**.

## Lokacje

| ID | Angielski | Polski |
|---:|---|---|
| 1180 | Weeping Wastes | Płaczące Pustkowia |
| 1181 | Pearl's Shrine | Sanktuarium Pearl |
| 1182 | Sculptor's Peak | Szczyt Rzeźbiarza |
| 1183 | Palace Entryway | Przedsionek Pałacu |
| 1184 | The Tower | Wieża |
| 1185 | Mira's Basement | Piwnica Miry |
| 1186 | Neon Crest | Neonowa Grań |
| 1187 | The Edge | Krawędź |
| 1188 | MoldStone Pass | Przełęcz Spleśniałego Kamienia (sprawdzić layout) |
| 1189 | The Back | Zaplecze |
| 1190 | Demon's Point | Cypel Demona |
| 1191 | The Cellar | Piwniczka (odróżnić od Piwnicy Miry) |
| 1192 | The Palace | Pałac |
| 1193 | Frosty Ridge | Mroźna Grań |
| 1194 | Kendra's Crying Chamber | Komnata Płaczu Kendry |
| 1195 | The Sinner's Inn | Gospoda Grzeszników |
| 1196 | The Market | Targ |

## Sprzątanie i śmieci

Nie ujednolicać celowo zróżnicowanego słownictwa:

- garbage — **śmieci**;
- trash — **śmieci**, czasem potocznie **graty**;
- grime — **brud** lub **nalot**, zależnie od powierzchni;
- mess — **bałagan**;
- dump (miejsce) — **śmietnik** lub **wysypisko**;
- `dumped back out` i podobne czasowniki tłumaczyć znaczeniowo: „wyrzucono”.

## UI, sterowanie i system

| Angielski | Polski |
|---|---|
| START | START |
| OPTIONS | OPCJE |
| EXIT | WYJDŹ |
| SAVE AND EXIT | ZAPISZ I WYJDŹ |
| RESUME | WZNÓW |
| RETURN TO MENU | WRÓĆ DO MENU |
| RETURN TO SHRINE | WRÓĆ DO SANKTUARIUM |
| BACK | WSTECZ |
| PAUSED | PAUZA |
| LANGUAGE | JĘZYK |
| CREDITS | TWÓRCY |
| ON / OFF | WŁ. / WYŁ. |
| YES / NO | TAK / NIE |
| CONTROLS | STEROWANIE |
| CONTROLLER | KONTROLER |
| KEYBOARD AND MOUSE | KLAWIATURA I MYSZ |
| JUMP | SKOK |
| ATTACK | ATAK |
| SPECIAL ATTACK | ATAK SPECJALNY |
| DASH | ZRYW |
| INTERACT | INTERAKCJA |
| SPLIT | ROZSZCZEPIENIE |
| MAP | MAPA |
| GRAPHICS | GRAFIKA |
| AUDIO | DŹWIĘK |
| GAMEPLAY | ROZGRYWKA |
| ASSIST | ASYSTA |
| FULLSCREEN | PEŁNY EKRAN |
| RESOLUTION | ROZDZIELCZOŚĆ |
| SCREEN FLASH | BŁYSKI EKRANU |
| SCREEN SHAKE | WSTRZĄSY EKRANU |
| TEXT EFFECTS | EFEKTY TEKSTU |
| MINI MAP | MINIMAPA |
| MASTER VOLUME | GŁOŚNOŚĆ GŁÓWNA |
| MUSIC VOLUME | GŁOŚNOŚĆ MUZYKI |
| SOUND VOLUME | GŁOŚNOŚĆ EFEKTÓW |
| PHOTOSENSITIVE MODE | REDUKCJA BŁYSKÓW |
| SPEEDRUN MODE | TRYB SPEEDRUN |
| SHUFFLE MODE | TRYB LOSOWY |
| NORMAL MODE | TRYB ZWYKŁY |
| BOSS RUSH | MARATON BOSSÓW |

## Przedmioty i umiejętności

- Upgrade Point(s) — **Punkt/Punkty ulepszeń**.
- Jukebox Disc — **Płyta do szafy grającej**.
- Film Reel — **Rolka taśmy filmowej**.
- Heart Capsule — **Kapsuła serca**.
- Air Dash — **Powietrzny zryw**.
- Spirit Split — **Rozszczepienie duszka**.
- Spirit Jump — **Skok duszka**.
- Dodge Roll — **Przewrót**; opis może doprecyzować unik.
- Game Saved! — **Gra zapisana!**
- Challenge Complete! — **Wyzwanie ukończone!**
- Challenge Failed... — **Wyzwanie nieudane...**
- Curse Lifted! — **Klątwa zdjęta!**

## Humor i kwestie otwarte

- Kalambury odtwarzać po polsku, nie objaśniać ich w dialogu.
- `Palace-less` wymaga rozwiązania kontekstowego; roboczo „bezpałacowy”.
- `Broomerang` ma ustalone **Miotlomerang**.
- Tytuły-parodie, np. `GHOST SLAUGHTER 3`, zachować przesadne i zwarte.
- Tagi animacji powinny obejmować polski odpowiednik tego samego słowa lub żartu.
- ID 1270 pozostaje poza partiami i wymaga osobnej decyzji technicznej.
