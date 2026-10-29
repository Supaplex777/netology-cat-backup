# Интеграционная проверка на Windows 11 (PowerShell)

Инструкция для реальной загрузки картинки на Яндекс.Диск на своём
компьютере. Все команды выполняются в **PowerShell** (Пуск → «Terminal» или
«Windows PowerShell»).

> Пока Pull Request не объединён, весь код находится в ветке
> `feature/initial-implementation`. В `main` пока только `.gitignore`,
> поэтому используйте именно рабочую ветку.

## 0. Что понадобится

- Python 3.11 или новее. Проверка:

  ```powershell
  py --version
  ```

  Если команда не найдена: `winget install Python.Python.3.13`, затем
  откройте новое окно PowerShell.
- Git (необязательно). Проверка: `git --version`. Установка:
  `winget install Git.Git`.
- **Новый** OAuth-токен Яндекс.Диска. Прежний токен был опубликован в
  переписке и считается раскрытым — не используйте его.

## 1. Скачать проект

С Git:

```powershell
cd $HOME\Documents
git clone --branch feature/initial-implementation https://github.com/Supaplex777/netology-cat-backup.git
cd netology-cat-backup
git branch --show-current     # должно быть: feature/initial-implementation
```

Без Git:

```powershell
cd $HOME\Documents
Invoke-WebRequest -Uri "https://github.com/Supaplex777/netology-cat-backup/archive/refs/heads/feature/initial-implementation.zip" -OutFile cat-backup.zip
Expand-Archive cat-backup.zip -DestinationPath .
cd netology-cat-backup-feature-initial-implementation
```

## 2. Виртуальное окружение и зависимости

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Если PowerShell пишет, что выполнение сценариев отключено, разрешите его
только для текущего окна и повторите активацию:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

В начале строки появится `(.venv)`. Дальше:

```powershell
$env:PYTHONUTF8 = "1"          # кириллица в выводе и файлах без искажений
python -m pip install -r requirements.txt
python -m pytest               # ожидается: все тесты passed, токен не нужен
```

## 3. Добавить токен локально

1. Получите токен на [Полигоне Яндекс.Диска](https://yandex.ru/dev/disk/poligon/)
   (кнопка «Получить OAuth-токен»).
2. Создайте `.env` из шаблона и откройте его в Блокноте:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

3. Впишите токен после знака `=` без кавычек и пробелов, сохраните файл:

   ```text
   YANDEX_DISK_TOKEN=ваш_новый_токен
   ```

4. Убедитесь, что Git не видит `.env` (если проект скачан через Git):

   ```powershell
   git check-ignore -v .env      # должно показать правило из .gitignore
   git status --short            # .env в списке быть не должно
   ```

Токен нигде не печатается: программа его не выводит, а `.env` исключён из
Git.

## 4. Запуск и загрузка настоящей картинки

```powershell
python main.py "Привет котик"
$LASTEXITCODE                  # 0 — успех
```

Ожидаемые строки в выводе:

- `Папка «AIE-9» создана` (при первом запуске) или `уже существует`;
- `Статус операции (проверка N): success`;
- `Файл загружен: Привет котик.jpg, размер … байт`;
- `Сведения сохранены в result.json` и `Готово!`.

Если Диск определит другой формат, файл будет переименован (например, в
`.png`) — это тоже отразится в выводе.

## 5. Проверить картинку на Диске

1. Откройте <https://disk.yandex.ru/client/disk/AIE-9>.
2. Убедитесь, что в папке `AIE-9` есть файл `Привет котик.jpg` (или с
   другим расширением, как в выводе программы).
3. Откройте картинку: на ней должна быть кошка с надписью «Привет котик».
4. В свойствах файла сравните размер с выводом программы.

## 6. Проверить `result.json`

```powershell
Get-Content result.json -Encoding UTF8
```

Должна быть запись вида (размер ваш, фактический):

```json
[
  {
    "file_name": "Привет котик.jpg",
    "size": 48213
  }
]
```

Размер в `result.json` должен совпадать с размером файла на Диске.

## 7. Проверить повторное имя

```powershell
python main.py "Привет котик"
Get-Content result.json -Encoding UTF8
```

Ожидается предупреждение о существующем файле и новый файл
`Привет котик (1).jpg`. Первый файл не перезаписывается, в `result.json`
теперь две записи.

## 8. Проверка ошибок без настоящего токена

Переменная окружения важнее `.env`, поэтому можно временно подставить
заведомо неверное значение — настоящий токен при этом не используется и не
показывается:

```powershell
$env:YANDEX_DISK_TOKEN = "invalid-token-for-test"
python main.py "Проверка ошибки"
$LASTEXITCODE                  # 1, сообщение «токен недействителен… (HTTP 401)»
Remove-Item Env:YANDEX_DISK_TOKEN
```

Пустой текст и отсутствие ввода:

```powershell
python main.py "   "
$LASTEXITCODE                  # 2 — «Текст надписи не может быть пустым»

cmd /c "python main.py < NUL"
$LASTEXITCODE                  # 3 — «Не удалось прочитать ввод», без traceback
```

Ни в одном из этих случаев новая запись в `result.json` не появляется.

## 9. Как безопасно передать результаты техническому руководителю

Можно отправлять:

- текст из окна PowerShell после запуска (программа не печатает токен);
- значения `$LASTEXITCODE`;
- содержимое `result.json`;
- вывод `python -m pytest` (последняя строка с числом тестов);
- скриншот папки `AIE-9` и открытой картинки на Диске.

Нельзя отправлять:

- файл `.env` и его содержимое;
- значение `YANDEX_DISK_TOKEN`, скриншоты страницы Полигона с токеном;
- скриншоты окна, где виден токен (например, открытый Блокнот с `.env`).

Перед отправкой бегло просмотрите текст: в нём не должно быть строки,
начинающейся с `y0_`, и слова `OAuth` с длинным значением.

## 10. После проверки

Если токен больше не нужен, отзовите доступ приложения Полигона в
настройках безопасности [Яндекс ID](https://id.yandex.ru/) (раздел с
доступами приложений) и удалите `.env`:

```powershell
Remove-Item .env
```
