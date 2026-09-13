# Garmin barbell lifts analyst

Lokalne narzedzie do pobrania z Garmin Connect serii trzech cwiczen z ostatnich
365 dni:

- `Barbell Deadlift` (`BARBELL_DEADLIFT`),
- `Barbell Back Squat` (`BARBELL_BACK_SQUAT`),
- `Barbell Bench Press` (`BARBELL_BENCH_PRESS`).

Skrypt uzywa nieoficjalnego API Garmin Connect przez `python-garminconnect`
i endpoint serii:

`/activity-service/activity/{activityId}/exerciseSets`

Nie korzysta z eksportu FIT ani zbiorczego CSV jako zrodla nazw cwiczen.

Garmin przyjmuje w filtrze aktywnosci typ nadrzedny `fitness_equipment`.
Skrypt pobiera ten typ i lokalnie wybiera rekordy z podtypem
`activityType.typeKey == strength_training`.

## Instalacja

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## Pierwsze uruchomienie

```bash
. .venv/bin/activate
python garmin_deadlift_progress.py all
```

Jesli nie ma jeszcze waznych tokenow, skrypt poprosi w terminalu o email, haslo
i ewentualny kod MFA. Nie wpisuj danych logowania w czacie. Tokeny sa zapisywane
w `.garmin_tokens/`, ktory jest ignorowany przez Git.

## Aktualizacja danych i dashboardu

Kolejne uruchomienia pobieraja tylko dane od ostatniej udanej synchronizacji,
z siedmiodniowa zakladka na poprawki wykonane w Garmin Connect. Nastepnie skrypt
przelicza wyniki i aktualizuje statyczny JSON dashboardu:

```bash
. .venv/bin/activate
python garmin_deadlift_progress.py pull
```

Pelne pobranie ostatnich 365 dni mozna wymusic opcja `--full-refresh`.

## Ponowna analiza bez pobierania z Garmina

Po pierwszym pobraniu mozna przeliczyc CSV, tabele i wykresy z lokalnego JSON-a:

```bash
. .venv/bin/activate
python garmin_deadlift_progress.py analyze --raw-json exports/raw/garmin_strength_raw_latest.json
```

## Wyniki

Skrypt zapisuje pliki w `exports/`. Dla kazdego identyfikatora `deadlift`,
`back_squat` i `bench_press` powstaja:

- `exports/raw/` - surowy JSON z lista aktywnosci i odpowiedziami `exerciseSets`;
- `exports/processed/<identyfikator>_sets.csv` - wszystkie serie;
- `exports/processed/<identyfikator>_workouts_summary.csv` - podsumowanie treningow;
- `exports/processed/<identyfikator>_analysis_report.json` - walidacja i progres;
- `exports/plots/<identyfikator>_max_weight_by_workout.png` - maksymalny ciezar
  w kazdym treningu.

Zbiorczy raport trzech cwiczen jest zapisywany jako
`exports/processed/strength_analysis_report.json`. Skrypt nie generuje innych
wykresow. Publiczny, pozbawiony prywatnych identyfikatorow snapshot powstaje w
`dashboard/data/strength-progress.json`.

## GitHub Pages

Workflow `.github/workflows/update-dashboard.yml` uruchamia sie recznie przez
zakladke Actions. Odtwarza token Garmina z sekretu repozytorium
`GARMIN_TOKENS_B64`, pobiera siedmiodniowe okno zmian, scala je z historia w
publicznym JSON-ie i publikuje wylacznie katalog `dashboard/` przez GitHub Pages.
Surowe odpowiedzi API i tokeny nie sa zapisywane w repozytorium ani artefakcie
Pages.

W odpowiedziach endpointu `exerciseSets` pole `weight` jest zapisane w gramach.
Skrypt dzieli je przez 1000 i zapisuje wynik w kilogramach; zrodlo, wartosc
oryginalna, jednostka i sposob konwersji pozostaja widoczne w CSV.

Braki ciezaru lub powtorzen pozostaja puste. Skrypt nie podstawia zer i nie
usuwa automatycznie serii rozgrzewkowych.
