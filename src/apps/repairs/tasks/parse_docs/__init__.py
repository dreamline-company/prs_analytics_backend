import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from core.settings import get_settings

# Принудительный обход проверки платформы Node.js
os.environ.setdefault("NODE_SKIP_PLATFORM_CHECK", "1")

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

settings = get_settings()

LOGIN_URL = "https://abai.kmg.kz/ru/login"
WELL_CARD_URL = "https://abai.kmg.kz/ru/bigdata/well-card"
DEFAULT_DOMAIN_LABEL = "@emg.kz(new)"


@dataclass(frozen=True)
class PRSActConfig:
    login: str
    password: str
    domain_label: str = DEFAULT_DOMAIN_LABEL
    headless: bool = False
    browser_channel: str = "chrome"
    default_timeout_ms: int = 30_000
    download_timeout_ms: int = 20_000
    viewport_width: int = 1920
    viewport_height: int = 1080
    output_folder_name: str = "well_PRS"
    not_found_filename: str = "not_found_wells_prs.txt"
    log_filename: str = "prs_bot.log"


@dataclass(frozen=True)
class PdfTask:
    link: Locator
    href: str | None
    file_dates: str


class LoadPRSACT:
    def __init__(
        self,
        config: PRSActConfig | None = None,
        script_dir: Path | str | None = None,
        *,
        headless: bool = True,
    ) -> None:
        self.script_dir = (
            Path(script_dir).resolve()
            if script_dir
            else Path(__file__).resolve().parent
        )
        self.config = config or self._load_config_from_settings()

        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

        self.well_base_folder = self.script_dir / self.config.output_folder_name
        self.not_found_file = self.script_dir / self.config.not_found_filename

    # =========================
    # Public API
    # =========================

    def run(self) -> None:
        self.setup_logging()
        self.start_browser()

        try:
            self.login()

            if not self.open_prs_tab():
                return

            print("\n" + "=" * 60)
            print("✅ Бот ПРС успешно запущен")
            print("Для выхода введите: q, exit, quit, выход")
            print("=" * 60)

            while True:
                current_well = input("\nВведите номер скважины: ").strip()

                if current_well.lower() in {"q", "й", "exit", "quit", "выход"}:
                    logging.info("Завершение работы программы.")
                    break

                if not current_well:
                    continue

                self.process_well(current_well)

        finally:
            self.close()

    def process_well(self, well_name: str) -> None:
        self._require_page()

        logging.info("Начинаю поиск ПРС: скважина %s", well_name)

        try:
            if not self.select_well(well_name):
                logging.info("Скважина %s не найдена в системе.", well_name)
                self.save_not_found_well(well_name, "скважина не найдена в системе")
                return

            self.wait_table_loaded()
            self.scroll_table_down()
            tasks = self.collect_pdf_tasks()

            if not tasks:
                logging.info("Акты ПРС PDF не найдены для скважины %s.", well_name)
                self.save_not_found_well(well_name, "Акты ПРС PDF не найдены")
                return

            well_folder = self.well_base_folder / self.safe_filename(well_name)
            well_folder.mkdir(parents=True, exist_ok=True)

            self.download_tasks(
                tasks=tasks,
                well_folder=well_folder,
                well_name=well_name,
            )

            logging.info("Обработка скважины %s завершена.", well_name)
            print("-" * 60)

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logging.exception("Ошибка при обработке скважины %s: %s", well_name, exc)
            self.save_not_found_well(well_name, f"ошибка обработки: {exc}")

    def start_browser(self) -> None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(self.script_dir / "ms-playwright")
        self.well_base_folder.mkdir(parents=True, exist_ok=True)

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            channel=self.config.browser_channel,
            headless=self.config.headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                f"--window-size={self.config.viewport_width},{self.config.viewport_height}",
                "--allow-running-insecure-content",
                "--disable-web-security",
            ],
        )

        self.context = self.browser.new_context(
            viewport={
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            },
            locale="ru-RU",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
            accept_downloads=True,
        )

        self.page = self.context.new_page()
        self.page.set_default_timeout(self.config.default_timeout_ms)
        self.page.set_default_navigation_timeout(self.config.default_timeout_ms)

    def close(self) -> None:
        if self.context is not None:
            self.context.close()
            self.context = None

        if self.browser is not None:
            self.browser.close()
            self.browser = None

        if self.playwright is not None:
            self.playwright.stop()
            self.playwright = None

        self.page = None

    def __enter__(self) -> "LoadPRSACT":
        self.setup_logging()
        self.start_browser()
        self.login()
        self.open_prs_tab()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    # =========================
    # Config / settings
    # =========================

    def _load_config_from_settings(self, *, headless: bool) -> PRSActConfig:
        login = settings.ABAI_LOGIN
        password = settings.ABAI_PASS

        if not login or not password:
            raise RuntimeError("В settings не заданы ABAI_LOGIN и/или ABAI_PASSWORD.")

        return PRSActConfig(
            login=str(login),
            password=str(password),
            domain_label=str(DEFAULT_DOMAIN_LABEL),
            headless=headless,
            browser_channel="chrome",
            default_timeout_ms=30_000,
            download_timeout_ms=20_000,
            viewport_width=1920,
            viewport_height=1080,
            output_folder_name="well_PRS",
            not_found_filename="not_found_wells_prs.txt",
            log_filename="prs_bot.log",
        )

    # =========================
    # Logging / utility methods
    # =========================

    def setup_logging(self) -> None:
        log_file = self.script_dir / self.config.log_filename

        root_logger = logging.getLogger()
        if root_logger.handlers:
            return

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(log_file, encoding="utf-8"),
            ],
        )

    @staticmethod
    def safe_filename(value: str) -> str:
        value = str(value).strip()
        value = re.sub(r'[\\/*?:"<>|]', "_", value)
        value = re.sub(r"\s+", "_", value)
        value = value.strip("._ ")
        return value or "unknown"

    @staticmethod
    def unique_path(path: Path) -> Path:
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix
        parent = path.parent

        counter = 2
        while True:
            candidate = parent / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def is_visible_quick(locator: Locator, timeout_ms: int = 1_000) -> bool:
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:
            return False

    @staticmethod
    def safe_inner_text(locator: Locator, timeout_ms: int = 1_000) -> str:
        try:
            return locator.inner_text(timeout=timeout_ms).strip()
        except Exception:
            return ""

    @staticmethod
    def extract_dates_from_text(text: str) -> tuple[str, str]:
        dates = re.findall(r"\d{2}\.\d{2}\.\d{4}", text or "")
        start_date = dates[0] if len(dates) >= 1 else "без_даты_начала"
        end_date = dates[1] if len(dates) >= 2 else "без_даты_окончания"
        return start_date, end_date

    def save_not_found_well(self, well_name: str, reason: str = "") -> None:
        with self.not_found_file.open("a", encoding="utf-8") as file:
            file.write(f"{well_name} - {reason}\n")

    def _require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError(
                "Playwright page не инициализирован. Сначала вызови start_browser().",
            )
        return self.page

    def _require_context(self) -> BrowserContext:
        if self.context is None:
            raise RuntimeError(
                "Playwright context не инициализирован. Сначала вызови start_browser().",
            )
        return self.context

    # =========================
    # Site actions
    # =========================

    def login(self) -> None:
        page = self._require_page()

        logging.info("ЭТАП 1: загрузка страницы входа")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        login_input = page.locator('input[placeholder="Вход"]').first
        try:
            login_input.wait_for(state="visible", timeout=20_000)
        except Exception as exc:
            screenshot_path = self.script_dir / "debug_login_screen_prs.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            raise RuntimeError(
                f"Поле 'Вход' не появилось. Скриншот сохранен: {screenshot_path}",
            ) from exc

        login_input.fill(self.config.login)
        page.select_option("select", label=self.config.domain_label)
        page.locator('input[placeholder="Password"]').first.fill(self.config.password)

        logging.info("Авторизация...")
        login_button = page.locator('button:has-text("ВОЙТИ")').first

        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                login_button.click()
        except PlaywrightTimeoutError:
            logging.warning(
                "Навигация после логина не зафиксирована, продолжаю проверку сессии.",
            )
            if self.is_visible_quick(login_button, 500):
                login_button.click(timeout=5_000)
            page.wait_for_timeout(2_000)

    def open_prs_tab(self) -> bool:
        page = self._require_page()

        logging.info("ЭТАП 2: открытие карточки скважины")
        page.goto(WELL_CARD_URL, wait_until="domcontentloaded")

        logging.info("ЭТАП 3: переход во вкладку ПРС")
        prs_item = page.get_by_text("ПРС", exact=True).first

        try:
            if not self.is_visible_quick(prs_item, 3_000):
                repair_item = page.get_by_text("Ремонт", exact=True).first
                repair_item.wait_for(state="visible", timeout=10_000)
                repair_item.click()

            prs_item.wait_for(state="visible", timeout=15_000)
            prs_item.scroll_into_view_if_needed()
            prs_item.click(force=True)
            page.wait_for_load_state("networkidle", timeout=15_000)
            return True
        except Exception as exc:
            logging.exception("Не удалось найти или открыть вкладку ПРС: %s", exc)
            return False

    def select_well(self, well_name: str) -> bool:
        page = self._require_page()

        search_input = page.get_by_placeholder("Номер скважины").first
        search_input.wait_for(state="visible", timeout=10_000)

        search_input.click()
        search_input.fill("")
        search_input.fill(well_name)
        page.wait_for_timeout(800)

        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1_000)

        not_found = page.locator(
            "text=/Sorry, no matching options|Ничего не найдено/i",
        ).first
        return not self.is_visible_quick(not_found, 1_000)

    def wait_table_loaded(self) -> None:
        page = self._require_page()

        logging.info("Жду обновления таблицы ПРС...")
        page.wait_for_timeout(2_500)

        try:
            loaders = page.locator(
                "[class*='loading'], [class*='loader'], [class*='spinner'], .progress-bar",
            )
            if self.is_visible_quick(loaders.first, 500):
                loaders.first.wait_for(state="hidden", timeout=15_000)
        except Exception:
            pass

        try:
            page.wait_for_load_state("networkidle", timeout=7_000)
        except Exception:
            pass

        try:
            page.wait_for_selector("thead tr th", state="visible", timeout=10_000)
        except Exception:
            pass

    def scroll_table_down(self) -> None:
        page = self._require_page()

        logging.info("Прокрутка таблицы вниз для подгрузки актов...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        page.evaluate(
            """
            () => {
                const selectors = [
                    '.v-table__wrapper',
                    '.el-table__body-wrapper',
                    '.ag-body-viewport',
                    'tbody',
                    '.table-responsive'
                ];

                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (el) el.scrollTop = el.scrollHeight;
                }
            }
            """,
        )
        page.wait_for_timeout(1_000)

    def collect_pdf_tasks(self) -> list[PdfTask]:
        page = self._require_page()

        logging.info("Сканирую строки на наличие PDF-актов ПРС...")

        tasks: list[PdfTask] = []
        seen: set[str] = set()
        links = page.locator("a")

        try:
            count = links.count()
        except Exception as exc:
            logging.warning("Не удалось получить список ссылок: %s", exc)
            return []

        for index in range(count):
            link = links.nth(index)

            if not self.is_visible_quick(link, 300):
                continue

            link_text = self.safe_inner_text(link, timeout_ms=500)
            href = link.get_attribute("href") or ""
            text_lower = link_text.lower()
            href_lower = href.lower()

            if ".pdf" not in text_lower and ".pdf" not in href_lower:
                continue

            row_text = ""
            try:
                row = link.locator(
                    "xpath=./ancestor::tr | "
                    "./ancestor::div[@role='row'] | "
                    "./ancestor::div[contains(@class, 'table-row')]",
                ).first
                if self.is_visible_quick(row, 300):
                    row_text = self.safe_inner_text(row, timeout_ms=1_000)
            except Exception:
                pass

            start_date, end_date = self.extract_dates_from_text(row_text)
            file_dates = f"{start_date}-{end_date}"

            dedupe_key = f"{href}|{link_text}|{file_dates}"
            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)
            tasks.append(PdfTask(link=link, href=href or None, file_dates=file_dates))

        return tasks

    # =========================
    # Download methods
    # =========================

    def download_tasks(
        self,
        tasks: list[PdfTask],
        well_folder: Path,
        well_name: str,
    ) -> None:
        page = self._require_page()
        safe_well_name = self.safe_filename(well_name)

        logging.info("Найдено PDF файлов: %s. Начинаю скачивание...", len(tasks))

        for task in tasks:
            filename = f"{safe_well_name}_ПРС_{self.safe_filename(task.file_dates)}.pdf"
            target_path = self.unique_path(well_folder / filename)

            downloaded = self.download_pdf_via_request(
                href=task.href,
                target_path=target_path,
            )

            if not downloaded:
                downloaded = self.download_pdf_via_click(
                    link=task.link,
                    target_path=target_path,
                )

            if downloaded:
                logging.info("Скачан: %s", target_path.name)
            else:
                logging.error("Не удалось скачать: %s", target_path.name)

            page.wait_for_timeout(700)

    def download_pdf_via_request(self, href: str | None, target_path: Path) -> bool:
        page = self._require_page()
        context = self._require_context()

        if not href:
            return False

        if href.startswith("blob:") or href.startswith("javascript:"):
            return False

        url = urljoin(page.url, href)

        try:
            response = context.request.get(url, timeout=self.config.download_timeout_ms)
            if not response.ok:
                logging.warning("HTTP %s при скачивании %s", response.status, url)
                return False

            body = response.body()
            content_type = (response.headers.get("content-type") or "").lower()

            looks_like_pdf = (
                body.startswith(b"%PDF")
                or "application/pdf" in content_type
                or ".pdf" in url.lower()
            )

            if not looks_like_pdf:
                logging.warning(
                    "Ответ по ссылке не похож на PDF: content-type=%s, url=%s",
                    content_type,
                    url,
                )
                return False

            target_path.write_bytes(body)
            return True
        except Exception as exc:
            logging.warning("Не удалось скачать PDF прямым запросом: %s", exc)
            return False

    def download_pdf_via_click(self, link: Locator, target_path: Path) -> bool:
        page = self._require_page()

        try:
            link.evaluate("node => node.removeAttribute('target')")
            page.wait_for_timeout(500)

            with page.expect_download(
                timeout=self.config.download_timeout_ms,
            ) as download_info:
                link.click(modifiers=["Alt"], force=True)

            download = download_info.value
            download.save_as(str(target_path))
            return True
        except Exception as exc:
            logging.warning("Не удалось скачать PDF через клик: %s", exc)
            return False


def run_prs_bot() -> None:
    LoadPRSACT().run()


if __name__ == "__main__":
    run_prs_bot()
