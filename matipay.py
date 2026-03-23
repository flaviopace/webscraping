import os
import sys
import json
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from telegram import Update
from telegram import constants as botconst
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from bs4 import BeautifulSoup
import datetime


JSON_FILE = 'config.json'

telegramcmd = {
    "oggi"       : "today",
    "ieri"       : "yesterday",
    "ultimi7gg"  : "last7",
    "ultimi30gg" : "last30",
}

enumdate = {
    "today"     : 1,
    "yesterday" : 2,
    "last7"     : 3,
    "last30"    : 4,
    "thismonth" : 5,
    "lastmonth" : 6
}

ch_id = ''


def getMatiPayCredentials():
    with open(os.path.join(sys.path[0], JSON_FILE), 'r') as in_file:
        conf = json.load(in_file)
    user = conf['matipay']['user']
    passwd = conf['matipay']['pass']
    hostname = conf['matipay']['hostname']
    return user, passwd, hostname


class matipay:

    def __init__(self, host, username, password, headless=True):
        self.username = username
        self.password = password
        self.hostname = host

        options = Options()
        if headless:
            options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        self.driver = webdriver.Chrome(options=options)

        self.login()

    def login(self):
        self.driver.get(self.hostname)
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "username"))
            )
            self.driver.find_element(By.ID, "username").send_keys(self.username)
            self.driver.find_element(By.ID, "password").send_keys(self.password)
            self.driver.find_element(By.ID, "submit").click()
            # Wait for dashboard to load after login
            WebDriverWait(self.driver, 10).until(
                EC.url_changes(self.hostname)
            )
            time.sleep(2)
        except Exception as e:
            print('Failed to Login: {}'.format(e))

    def goto_vm(self, serial_number):
        base_url = self.hostname.replace('/login', '')
        self.driver.get(base_url + '/vm')
        try:
            # Wait for the filter table to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "inputserialnumber"))
            )
            # Type the serial number in the Matricola filter
            sn_input = self.driver.find_element(By.ID, "inputserialnumber")
            sn_input.clear()
            sn_input.send_keys(serial_number)
            # Click "Cerca" to apply the filter
            self.driver.find_element(By.CSS_SELECTOR, "button[ng-click='applyFilters()']").click()
            # Wait for Angular to re-render the table with filtered results
            time.sleep(2)
            # Re-find the link after Angular has settled, then click
            link = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.LINK_TEXT, serial_number))
            )
            link.click()
            # Wait for vm-details page to load
            WebDriverWait(self.driver, 10).until(
                EC.url_contains('vm-details')
            )
            time.sleep(2)
            print("Navigated to:", self.driver.current_url)
        except Exception as e:
            print('Failed to navigate to VM {}: {}'.format(serial_number, e))

    def get_cash_transactions(self, date=None):
        if date is None:
            date = datetime.date.today().strftime('%Y-%m-%d')
        try:
            # Step 1: Click the "Transazioni" tab
            print("Step 1: clicking Transazioni tab")
            WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[normalize-space()='Transazioni']")
                )
            ).click()
            time.sleep(2)

            # Step 2: Click "Transazioni Distributore"
            print("Step 2: clicking Transazioni Distributore")
            WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//h4[normalize-space()='Transazioni Distributore']")
                )
            ).click()
            time.sleep(2)

            # Step 3: Click "Cash" payment type
            print("Step 3: clicking Cash")
            WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//span[contains(@ng-click,\"loadData('CASH')\")]")
                )
            ).click()
            time.sleep(2)

            # Step 4: Wait for date filter form to appear
            print("Step 4: waiting for date inputs")
            WebDriverWait(self.driver, 10).until(
                EC.visibility_of_element_located((By.ID, "inputvmtimemin"))
            )

            # Set date from and date to (both = today)
            for field_id in ("inputvmtimemin", "inputvmtimemax"):
                field = self.driver.find_element(By.ID, field_id)
                field.clear()
                field.send_keys(date)

            # Step 5: Click "Cerca"
            print("Step 5: clicking Cerca")
            self.driver.find_element(
                By.CSS_SELECTOR, "button.btn-search[ng-click='applyFilters()']"
            ).click()
            time.sleep(2)

            # Step 6: Wait for the table rows to appear
            print("Step 6: waiting for results")
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "tr[ng-repeat='t in data.list']")
                )
            )
            time.sleep(1)

            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            return self.parse_transactions(soup)

        except Exception as e:
            print('Failed at step - {}'.format(e))
            return []

    @staticmethod
    def parse_transactions(soup):
        table = soup.find('tr', attrs={'ng-repeat': 't in data.list'})
        if not table:
            return []
        tbody = table.find_parent('tbody')
        rows = []
        for row in tbody.find_all('tr'):
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if cells:
                rows.append(cells)
        return rows

    def get_daily_total(self, date=None):
        transactions = self.get_cash_transactions(date=date)
        total = 0.0
        for row in transactions:
            try:
                # Amount is column index 3, Italian format e.g. "1,50" or "1.234,56"
                amount_str = row[3].replace('.', '').replace(',', '.')
                total += float(amount_str)
            except (IndexError, ValueError):
                pass
        return round(total, 2), len(transactions)

    def close(self):
        self.driver.quit()


def end_of_month(dt):
    todays_month = dt.month
    tomorrows_month = (dt + datetime.timedelta(days=1)).month
    return tomorrows_month != todays_month


async def cmdhandler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msginput = update.message.text.replace('/', '')
    input = telegramcmd[msginput]

    user, passwd, hostname = getMatiPayCredentials()

    await update.message.reply_text("Attendi qualche secondo, sto collezionando i dati ...")
    conn = matipay(host=hostname, username=user, password=passwd)
    # TODO: add stat collection once dashboard HTML is analysed
    conn.close()


async def callback_once(context: ContextTypes.DEFAULT_TYPE):
    user, passwd, hostname = getMatiPayCredentials()

    query_list = []
    keys = list(telegramcmd.keys())
    query_list.append(keys[0])  # only today

    now = datetime.datetime.now()
    weekno = now.weekday()

    if end_of_month(now):
        print("is last day of the month")
        query_list.append(keys[3])  # last 30 days
    if weekno == 6:
        print("is sunday")
        query_list.append(keys[2])  # last 7 days

    for key in query_list:
        await context.bot.send_message(
            chat_id=ch_id,
            text="Sto collezionando i dati per: *{}*".format(key),
            parse_mode=botconst.ParseMode.MARKDOWN_V2
        )
        option = telegramcmd[key]
        conn = matipay(host=hostname, username=user, password=passwd)
        # TODO: add stat collection once dashboard HTML is analysed
        conn.close()


class MatiPayBot:
    def __init__(self, tokenid):
        self.app = ApplicationBuilder().token(tokenid).build()

        self.app.add_handler(CommandHandler("oggi", cmdhandler))
        self.app.add_handler(CommandHandler("ieri", cmdhandler))
        self.app.add_handler(CommandHandler("ultimi7gg", cmdhandler))

        job_queue = self.app.job_queue
        job_queue.run_once(callback_once, when=5)

        self.app.run_polling()


def testlogin():
    user, passwd, hostname = getMatiPayCredentials()
    conn = matipay(host=hostname, username=user, password=passwd, headless=False)
    print("Current URL after login:", conn.driver.current_url)
    conn.goto_vm('000001')
    print("Current URL after vm navigation:", conn.driver.current_url)
    total, count = conn.get_daily_total()
    print("Transactions found: {}  |  Total: €{:.2f}".format(count, total))
    conn.close()


if __name__ == '__main__':
    testlogin()
