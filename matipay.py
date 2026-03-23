import os
import sys
import json
import datetime
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram import constants as botconst
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


JSON_FILE = 'config.json'
BASE_URL  = 'https://vendingapp.matipay.com'
APP_ROOT  = '/smart-vending-webapp'

telegramcmd = {
    "oggi"       : "today",
    "ieri"       : "yesterday",
    "ultimi7gg"  : "last7",
    "ultimi30gg" : "last30",
}

ch_id = ''


def getMatiPayCredentials():
    with open(os.path.join(sys.path[0], JSON_FILE)) as f:
        conf = json.load(f)
    return conf['matipay']['user'], conf['matipay']['pass'], conf['matipay']['hostname']


def date_range(period):
    today = datetime.date.today()
    if period == 'today':
        return today, today
    elif period == 'yesterday':
        d = today - datetime.timedelta(days=1)
        return d, d
    elif period == 'last7':
        return today - datetime.timedelta(days=6), today
    elif period == 'last30':
        return today - datetime.timedelta(days=29), today
    return today, today


class MatiPayAPI:

    def __init__(self, username, password):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0'})
        self._login(username, password)

    def _login(self, username, password):
        resp = self.session.get(BASE_URL + APP_ROOT + '/login')
        soup = BeautifulSoup(resp.text, 'html.parser')
        csrf_meta = soup.find('meta', {'id': '_csrf'})
        csrf = csrf_meta['content'] if csrf_meta else ''

        resp = self.session.post(
            BASE_URL + APP_ROOT + '/j_spring_security_check',
            data={'username': username, 'password': password, '_csrf': csrf},
            allow_redirects=True,
        )
        if 'login' in resp.url:
            raise Exception('Login failed — check credentials in config.json')
        print('Logged in:', resp.url)

    def get_transactions(self, serial_number, pv_cod, date_from, date_to,
                         transaction_table='CASH'):
        """Fetch all pages of transactions and return the raw list."""
        params = {
            'vmSerialNumber'    : serial_number,
            'pvCod'             : pv_cod,
            'transactionTable'  : transaction_table,
            'transactionTimeMin': str(date_from) + ' 00:00:00',
            'transactionTimeMax': str(date_to)   + ' 23:59:59',
            'orderBy'           : 'TRANSACTION_TIME',
            'orderType'         : 'DESC',
            'page'              : 1,
        }
        all_items = []

        while True:
            resp = self.session.get(
                BASE_URL + APP_ROOT + '/vm-details/transactions/list',
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            items = data.get('list', [])
            all_items.extend(items)

            total     = data.get('totalItems', 0)
            page_size = data.get('pageSize', len(items)) or 1

            if len(all_items) >= total or not items:
                break
            params['page'] += 1

        return all_items

    def get_daily_total(self, serial_number, pv_cod, period='today',
                        transaction_table='CASH'):
        date_from, date_to = date_range(period)
        transactions = self.get_transactions(
            serial_number, pv_cod, date_from, date_to, transaction_table
        )
        total = sum(float(t.get('amount', 0) or 0) for t in transactions)
        return round(total, 2), len(transactions)


def format_message(serial_number, period, total, count, transaction_table='CASH'):
    label = {
        'today'    : 'Oggi',
        'yesterday': 'Ieri',
        'last7'    : 'Ultimi 7 giorni',
        'last30'   : 'Ultimi 30 giorni',
    }.get(period, period)
    return (
        "Distributore: {}\n"
        "Periodo: {}\n"
        "Tipo: {}\n"
        "Transazioni: {}\n"
        "Totale: €{:.2f}"
    ).format(serial_number, label, transaction_table, count, total)


def end_of_month(dt):
    return (dt + datetime.timedelta(days=1)).month != dt.month


async def cmdhandler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msginput = update.message.text.replace('/', '')
    period = telegramcmd[msginput]

    user, passwd, _ = getMatiPayCredentials()
    await update.message.reply_text("Attendi, sto raccogliendo i dati...")

    api = MatiPayAPI(user, passwd)
    total, count = api.get_daily_total('000001', '0001', period)
    msg = format_message('000001', period, total, count)
    await update.message.reply_text(msg)


async def callback_once(context: ContextTypes.DEFAULT_TYPE):
    user, passwd, _ = getMatiPayCredentials()
    api = MatiPayAPI(user, passwd)

    query_list = ['oggi']
    now = datetime.datetime.now()
    if end_of_month(now.date()):
        query_list.append('ultimi30gg')
    if now.weekday() == 6:
        query_list.append('ultimi7gg')

    for key in query_list:
        period = telegramcmd[key]
        total, count = api.get_daily_total('000001', '0001', period)
        msg = format_message('000001', period, total, count)
        await context.bot.send_message(chat_id=ch_id, text=msg)


class MatiPayBot:
    def __init__(self, tokenid):
        self.app = ApplicationBuilder().token(tokenid).build()
        self.app.add_handler(CommandHandler("oggi",       cmdhandler))
        self.app.add_handler(CommandHandler("ieri",       cmdhandler))
        self.app.add_handler(CommandHandler("ultimi7gg",  cmdhandler))
        self.app.add_handler(CommandHandler("ultimi30gg", cmdhandler))

        self.app.job_queue.run_once(callback_once, when=5)
        self.app.run_polling()


def test():
    user, passwd, _ = getMatiPayCredentials()
    api = MatiPayAPI(user, passwd)

    for period in ('today', 'yesterday', 'last7'):
        total, count = api.get_daily_total('000001', '0001', period)
        print(format_message('000001', period, total, count))
        print()


if __name__ == '__main__':
    test()
