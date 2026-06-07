import os
import sys
import json
import collections
import datetime
import tempfile
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from bs4 import BeautifulSoup
from telegram import Update
from telegram import constants as botconst
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


JSON_FILE = 'config.json'
BASE_URL  = 'https://vendingapp.matipay.com'
APP_ROOT  = '/smart-vending-webapp'

telegramcmd = {
    "oggi"         : "today",
    "ieri"         : "yesterday",
    "ultimi7gg"    : "last7",
    "ultimi30gg"   : "last30",
    "questomese"   : "thismonth",
    "mescorso"     : "lastmonth",
}

def load_config():
    with open(os.path.join(sys.path[0], JSON_FILE)) as f:
        return json.load(f)

def getMatiPayCredentials():
    conf = load_config()
    return conf['matipay']['user'], conf['matipay']['pass'], conf['matipay']['hostname']

def getBotConfig():
    conf = load_config()
    return conf['matipay_bot_config']['token_id'], conf['matipay_bot_config']['channel_id']

def getMachineNames():
    conf = load_config()
    return conf.get('matipay_machine_names', {})

def getEmailConfig():
    conf = load_config()
    c = conf['email_config']
    return c['sender'], c['app_password'], c['recipients']


def send_email(graph_path, text):
    sender, app_password, recipients = getEmailConfig()
    today = datetime.date.today().strftime('%d/%m/%Y')

    msg = MIMEMultipart()
    msg['From']    = sender
    msg['To']      = ', '.join(recipients)
    msg['Subject'] = 'Incasso distributori - {}'.format(today)

    msg.attach(MIMEText(text, 'plain'))

    with open(graph_path, 'rb') as f:
        img = MIMEImage(f.read(), name='incasso_7gg.png')
    msg.attach(img)

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(sender, app_password)
        server.sendmail(sender, recipients, msg.as_string())
    print('Email sent to', recipients)


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
    elif period == 'thismonth':
        first = today.replace(day=1)
        return first, today
    elif period == 'lastmonth':
        last = today.replace(day=1) - datetime.timedelta(days=1)
        first = last.replace(day=1)
        return first, last
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
        """Fetch all pages of CASH transactions and return only PURCHASE (Acquisto) ones."""
        params = [
            ('vmSerialNumber',     serial_number),
            ('pvCod',              pv_cod),
            ('transactionTable',   transaction_table),
            ('transactionTimeMin', str(date_from) + ' 00:00:00'),
            ('transactionTimeMax', str(date_to)   + ' 23:59:59'),
            ('orderBy',            'TRANSACTION_TIME'),
            ('orderType',          'DESC'),
            ('page',               1),
        ]
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

            total = data.get('totalItems', 0)
            if len(all_items) >= total or not items:
                break
            params = [(k, v) for k, v in params if k != 'page']
            params.append(('page', len(all_items) // (data.get('pageSize') or 20) + 1))

        # Filter client-side: keep only "Acquisto - cash" (transactionType == PURCHASE)
        return [t for t in all_items if t.get('transactionType') == 'PURCHASE']

    def get_machines(self):
        """Return list of all vending machines as dicts with vmSerialNumber and pvCod."""
        all_items = []
        page = 1
        while True:
            resp = self.session.get(
                BASE_URL + APP_ROOT + '/vm/list',
                params={'page': page, 'orderBy': 'VM_SERIAL_NUMBER', 'orderType': 'ASC'},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get('list', [])
            all_items.extend(items)
            if len(all_items) >= data.get('totalItems', 0) or not items:
                break
            page += 1
        return [{'sn': m['vmSerialNumber'], 'pv': m['pvCod']} for m in all_items]

    def get_failed_transactions(self, serial_number, pv_cod, date_from, date_to):
        """Fetch all CASH transactions and return only failed (DENIED/INVALID) ones."""
        params = [
            ('vmSerialNumber',     serial_number),
            ('pvCod',              pv_cod),
            ('transactionTable',   'CASH'),
            ('transactionTimeMin', str(date_from) + ' 00:00:00'),
            ('transactionTimeMax', str(date_to)   + ' 23:59:59'),
            ('orderBy',            'TRANSACTION_TIME'),
            ('orderType',          'DESC'),
            ('page',               1),
        ]
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
            total = data.get('totalItems', 0)
            if len(all_items) >= total or not items:
                break
            params = [(k, v) for k, v in params if k != 'page']
            params.append(('page', len(all_items) // (data.get('pageSize') or 20) + 1))
        return [t for t in all_items if t.get('transactionStatus') in ('DENIED', 'INVALID', 'PENDING')]

    def get_daily_total(self, serial_number, pv_cod, period='today'):
        date_from, date_to = date_range(period)
        transactions = self.get_transactions(serial_number, pv_cod, date_from, date_to)
        total = sum(float(t.get('amount', 0) or 0) for t in transactions)
        return round(total, 2), len(transactions)

    def get_failed_total(self, serial_number, pv_cod, period='today'):
        date_from, date_to = date_range(period)
        failed = self.get_failed_transactions(serial_number, pv_cod, date_from, date_to)
        return len(failed)

    def get_monthly_trend(self):
        """Return monthly trend data from dashboard: {months: [...], qty: [...], amount: [...]}"""
        resp = self.session.get(
            BASE_URL + APP_ROOT + '/dashboard/get-graph',
            params={'graphType': 'VEND_GRAPH'},
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            'months': data['xAxis'],
            'qty':    data['yAxis'][0],
            'amount': data['yAxis'][1],
        }

    def get_all_machines_total(self, period='today'):
        """Return totals for every machine as a list of (sn, total, count, failed)."""
        results = []
        for m in self.get_machines():
            total, count = self.get_daily_total(m['sn'], m['pv'], period)
            failed = self.get_failed_total(m['sn'], m['pv'], period)
            results.append((m['sn'], total, count, failed))
        return results

    def get_week_over_week(self):
        """Return {sn: (this_week_total, last_week_total)} for each machine."""
        today = datetime.date.today()
        this_week_from = today - datetime.timedelta(days=6)
        last_week_from = today - datetime.timedelta(days=13)
        last_week_to   = today - datetime.timedelta(days=7)
        results = {}
        for m in self.get_machines():
            this_week = self.get_transactions(m['sn'], m['pv'], this_week_from, today)
            last_week = self.get_transactions(m['sn'], m['pv'], last_week_from, last_week_to)
            results[m['sn']] = (
                round(sum(float(t.get('amount', 0) or 0) for t in this_week), 2),
                round(sum(float(t.get('amount', 0) or 0) for t in last_week), 2),
            )
        return results

    def get_daily_breakdown(self, days=7):
        """Return {sn: [(date, total), ...]} for each machine over the last N days."""
        today = datetime.date.today()
        machines = self.get_machines()
        breakdown = {}
        for m in machines:
            daily = []
            for i in range(days - 1, -1, -1):
                day = today - datetime.timedelta(days=i)
                transactions = self.get_transactions(m['sn'], m['pv'], day, day)
                total = round(sum(float(t.get('amount', 0) or 0) for t in transactions), 2)
                daily.append((day, total))
            breakdown[m['sn']] = daily
        return breakdown

    def get_slot_and_hourly(self, period='last7'):
        """Aggregate sales by physical slot (vmSelectionConverted) and by hour of day.

        Returns (per_slot, per_hour):
          per_slot[sn] = list of {slot, code, qty, revenue} sorted by qty desc
          per_hour     = Counter {hour(0-23): n_sales} across all machines
        """
        date_from, date_to = date_range(period)
        per_slot = {}
        per_hour = collections.Counter()
        for m in self.get_machines():
            slots = {}
            for t in self.get_transactions(m['sn'], m['pv'], date_from, date_to):
                slot = str(t.get('vmSelectionConverted') or '?')
                code = str(t.get('productCode') or '?')
                row = slots.setdefault((slot, code), {
                    'slot': slot, 'code': code, 'qty': 0, 'revenue': 0.0})
                row['qty'] += 1
                row['revenue'] += float(t.get('amount') or 0)
                ts = t.get('transactionTime')
                if ts:
                    per_hour[datetime.datetime.fromtimestamp(ts / 1000).hour] += 1
            per_slot[m['sn']] = sorted(slots.values(), key=lambda r: r['qty'], reverse=True)
        return per_slot, per_hour

    def get_notifications(self):
        """Fetch all notifications (faults, sold-out, credit, etc.)."""
        resp = self.session.get(
            BASE_URL + APP_ROOT + '/notifications/list',
            params={'page': 1},
        )
        resp.raise_for_status()
        return resp.json()


def build_monthly_trend_graph(trend):
    """Build a dual-axis bar+line chart of monthly sales trend, return path to temp PNG."""
    months  = trend['months']
    amounts = trend['amount']
    qtys    = trend['qty']

    x = range(len(months))
    _, ax1 = plt.subplots(figsize=(10, 5))

    bars = ax1.bar(x, amounts, color='#2196F3', alpha=0.85, label='Incasso (€)')
    for bar, val in zip(bars, amounts):
        if val > 0:
            ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                     '€{:.0f}'.format(val), ha='center', va='bottom', fontsize=9)

    ax2 = ax1.twinx()
    ax2.plot(list(x), qtys, color='#FF9800', marker='o', linewidth=2, label='Vendite (n)')
    for xi, val in zip(x, qtys):
        if val > 0:
            ax2.text(xi, val + 0.3, '{:.0f}'.format(val),
                     ha='center', va='bottom', fontsize=8, color='#FF9800')

    ax1.set_xticks(list(x))
    ax1.set_xticklabels(months)
    ax1.set_ylabel('Incasso (€)', color='#2196F3')
    ax2.set_ylabel('Vendite (n)', color='#FF9800')
    ax1.set_title('Andamento mensile - Tutti i distributori')
    ax1.yaxis.grid(True, linestyle='--', alpha=0.4)
    ax1.set_axisbelow(True)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')

    plt.tight_layout()
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    plt.savefig(tmp.name, dpi=150)
    plt.close()
    return tmp.name


def build_graph(breakdown):
    """Build a bar chart of daily cash flow per machine, return path to temp PNG file."""
    names = getMachineNames()
    days = [d for d, _ in next(iter(breakdown.values()))]
    it_days = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
    day_labels = ['{}\n{}'.format(it_days[d.weekday()], d.strftime('%d/%m')) for d in days]

    x = range(len(days))
    width = 0.35
    n = len(breakdown)
    offsets = [i * width - (n - 1) * width / 2 for i in range(n)]

    _, ax = plt.subplots(figsize=(10, 5))
    colors = ['#2196F3', '#FF9800', '#4CAF50', '#E91E63']

    for idx, (sn, daily) in enumerate(breakdown.items()):
        totals = [t for _, t in daily]
        bars = ax.bar([xi + offsets[idx] for xi in x], totals,
                      width=width, label=names.get(sn, sn),
                      color=colors[idx % len(colors)], alpha=0.85)
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, h + 0.5,
                        f'€{h:.0f}', ha='center', va='bottom', fontsize=8)

    ax.set_xticks(list(x))
    ax.set_xticklabels(day_labels)
    ax.set_ylabel('Incasso (€)')
    ax.set_title('Incasso Cash - Ultimi 7 giorni')
    ax.yaxis.grid(True, linestyle='--', alpha=0.5)
    ax.set_axisbelow(True)

    ax.legend()
    plt.tight_layout()

    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    plt.savefig(tmp.name, dpi=150)
    plt.close()
    return tmp.name


def format_report(results, period):
    label = {
        'today'    : 'Oggi',
        'yesterday': 'Ieri',
        'last7'    : 'Ultimi 7 giorni',
        'last30'   : 'Ultimi 30 giorni',
        'thismonth': 'Questo mese',
        'lastmonth': 'Mese scorso',
    }.get(period, period)

    date_from, date_to = date_range(period)
    if date_from == date_to:
        date_str = date_from.strftime('%d/%m/%Y')
    else:
        date_str = '{} – {}'.format(date_from.strftime('%d/%m'), date_to.strftime('%d/%m/%Y'))

    names = getMachineNames()
    grand_total = 0.0
    grand_count = 0
    grand_failed = 0
    rows = []
    for sn, total, count, failed in results:
        rows.append((names.get(sn, sn), count, total, failed))
        grand_total += total
        grand_count += count
        grand_failed += failed

    # Dynamic column widths
    col1 = max(len(r[0]) for r in rows) + 1
    col1 = max(col1, len('Macchina'))
    sep = '─' * (col1 + 22)

    table  = '{:<{}} {:>7}  {:>9}  {}\n'.format('Macchina', col1, 'Vendite', 'Incasso', 'Errori')
    table += sep + '\n'
    for name, count, total, failed in rows:
        err = '⚠️ {}'.format(failed) if failed > 0 else '  -'
        table += '{:<{}} {:>7}  {:>9}  {}\n'.format(name, col1, count, '€{:.2f}'.format(total), err)
    table += sep + '\n'
    err_tot = '⚠️ {}'.format(grand_failed) if grand_failed > 0 else '  -'
    table += '{:<{}} {:>7}  {:>9}  {}'.format('TOTALE', col1, grand_count, '€{:.2f}'.format(round(grand_total, 2)), err_tot)

    lines = [
        '📊 {}  ({})'.format(label.upper(), date_str),
        '```',
        table,
        '```',
    ]
    return '\n'.join(lines)


def format_wow(wow):
    """Format week-over-week comparison report."""
    names = getMachineNames()
    today = datetime.date.today()
    this_from = (today - datetime.timedelta(days=6)).strftime('%d/%m')
    last_from = (today - datetime.timedelta(days=13)).strftime('%d/%m')
    last_to   = (today - datetime.timedelta(days=7)).strftime('%d/%m')

    grand_this, grand_last = 0.0, 0.0
    rows = []
    for sn, (this_week, last_week) in wow.items():
        name = names.get(sn, sn)
        if last_week > 0:
            pct = (this_week - last_week) / last_week * 100
            trend = '{:+.1f}%'.format(pct)
        else:
            trend = 'n/d'
        rows.append((name, this_week, last_week, trend))
        grand_this += this_week
        grand_last += last_week

    if grand_last > 0:
        pct = (grand_this - grand_last) / grand_last * 100
        total_trend = '{:+.1f}%'.format(pct)
    else:
        total_trend = 'n/d'

    col1 = max(len(r[0]) for r in rows) + 1
    col1 = max(col1, len('Macchina'))
    sep = '─' * (col1 + 26)

    table  = '{:<{}} {:>8}  {:>8}  {:>7}\n'.format('Macchina', col1, 'Questa', 'Prec.', 'Var.')
    table += sep + '\n'
    for name, this_w, last_w, trend in rows:
        table += '{:<{}} {:>8}  {:>8}  {:>7}\n'.format(
            name, col1, '€{:.2f}'.format(this_w), '€{:.2f}'.format(last_w), trend)
    table += sep + '\n'
    table += '{:<{}} {:>8}  {:>8}  {:>7}'.format(
        'TOTALE', col1, '€{:.2f}'.format(grand_this), '€{:.2f}'.format(grand_last), total_trend)

    lines = [
        '🔁 SETTIMANA SU SETTIMANA',
        'Questa: {} – {}   Prec: {} – {}'.format(
            this_from, today.strftime('%d/%m'), last_from, last_to),
        '```',
        table,
        '```',
    ]
    return '\n'.join(lines)


def format_top_slots(per_slot, top=10):
    """One Telegram message per machine: top selling slots (no product name)."""
    names = getMachineNames()
    blocks = []
    for sn, rows in per_slot.items():
        if not rows:
            continue
        lines = [
            '🏷️ {} — Top {} spirali (7gg)'.format(names.get(sn, sn), top),
            '```',
            'Spira  Cod   Qta   Incasso',
            '─' * 26,
        ]
        for r in rows[:top]:
            lines.append('{:>4}  {:>5}  {:>3}x  €{:>6.2f}'.format(
                r['slot'], r['code'], r['qty'], r['revenue']))
        lines.append('```')
        blocks.append('\n'.join(lines))
    return blocks


def format_hourly(per_hour):
    """Telegram message with a bar chart of sales per hour of day."""
    if not per_hour:
        return None
    peak = per_hour.most_common(1)[0][0]
    mx = max(per_hour.values())
    lines = ['🕐 Vendite per fascia oraria — 7gg (picco {:02d}:00)'.format(peak), '```']
    for h in range(24):
        n = per_hour.get(h, 0)
        bar = '█' * round(n / mx * 20) if mx else ''
        lines.append('{:02d}  {:<20} {}'.format(h, bar, n or ''))
    lines.append('```')
    return '\n'.join(lines)


NOTIF_LABELS = {
    'fault':     '🔴 Guasti',
    'soldOut':   '🟡 Sold-Out',
    'credit':    '💳 Credito',
    'take5':     '📦 Take5',
    'news':      '📰 News',
    'ecommerce': '🛒 E-commerce',
    'ocs':       '☕ OCS',
    'pagopa':    '🏛️ PagoPA',
    'giftCard':  '🎁 Gift Card',
}


def format_notifications(data):
    """Format notifications into a Telegram message."""
    total_badge = sum(cat.get('badge', 0) for cat in data.values())

    if total_badge == 0:
        return '✅ ALLARMI E NOTIFICHE\n\nNessun allarme attivo. Tutto OK!'

    lines = ['🚨 ALLARMI E NOTIFICHE\n']
    for key, cat in data.items():
        badge = cat.get('badge', 0)
        notifications = cat.get('notifications', [])
        if badge == 0:
            continue

        label = NOTIF_LABELS.get(key, key)
        lines.append('{} ({})'.format(label, badge))
        for n in notifications:
            vm_sn = n.get('vmSerialNumber', '')
            names = getMachineNames()
            vm_name = names.get(vm_sn, vm_sn)
            msg = n.get('message') or n.get('description') or n.get('text', '')
            ts = n.get('time') or n.get('timestamp')
            time_str = ''
            if ts:
                try:
                    dt = datetime.datetime.fromtimestamp(ts / 1000)
                    time_str = dt.strftime(' (%d/%m %H:%M)')
                except Exception:
                    pass
            detail = '  • {}'.format(vm_name) if vm_name else '  •'
            if msg:
                detail += ': {}'.format(msg)
            detail += time_str
            lines.append(detail)
        lines.append('')

    return '\n'.join(lines)


def end_of_month(dt):
    return (dt + datetime.timedelta(days=1)).month != dt.month


async def cmd_allarmi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, passwd, _ = getMatiPayCredentials()
    _, channel_id = getBotConfig()
    await update.message.reply_text("Attendi, controllo allarmi...")

    api = MatiPayAPI(user, passwd)
    data = api.get_notifications()
    msg = format_notifications(data)
    await update.message.reply_text(msg)
    await context.bot.send_message(chat_id=channel_id, text=msg)


async def cmdhandler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msginput = update.message.text.replace('/', '')
    period = telegramcmd[msginput]

    user, passwd, _ = getMatiPayCredentials()
    _, channel_id = getBotConfig()
    await update.message.reply_text("Attendi, sto raccogliendo i dati...")

    api = MatiPayAPI(user, passwd)
    results = api.get_all_machines_total(period)
    msg = format_report(results, period)
    await update.message.reply_text(msg)
    await context.bot.send_message(chat_id=channel_id, text=msg)


async def callback_once(context: ContextTypes.DEFAULT_TYPE):
    _, channel_id = getBotConfig()
    user, passwd, _ = getMatiPayCredentials()
    api = MatiPayAPI(user, passwd)

    query_list = ['oggi']
    now = datetime.datetime.now()
    if now.weekday() == 6:           # Sunday
        query_list.append('ultimi7gg')
    if end_of_month(now.date()):     # Last day of month
        query_list.append('questomese')

    for key in query_list:
        period = telegramcmd[key]
        results = api.get_all_machines_total(period)
        msg = format_report(results, period)
        await context.bot.send_message(chat_id=channel_id, text=msg)

    # Send notifications/alarms
    notif_data = api.get_notifications()
    notif_msg = format_notifications(notif_data)
    await context.bot.send_message(chat_id=channel_id, text=notif_msg)


class MatiPayBot:
    def __init__(self, tokenid):
        self.app = ApplicationBuilder().token(tokenid).build()
        self.app.add_handler(CommandHandler("oggi",        cmdhandler))
        self.app.add_handler(CommandHandler("ieri",        cmdhandler))
        self.app.add_handler(CommandHandler("ultimi7gg",   cmdhandler))
        self.app.add_handler(CommandHandler("ultimi30gg",  cmdhandler))
        self.app.add_handler(CommandHandler("questomese",  cmdhandler))
        self.app.add_handler(CommandHandler("mescorso",    cmdhandler))
        self.app.add_handler(CommandHandler("allarmi",     cmd_allarmi))

        self.app.job_queue.run_once(callback_once, when=5)
        self.app.run_polling()



def test():
    user, passwd, _ = getMatiPayCredentials()
    api = MatiPayAPI(user, passwd)

    for period in ('today', 'yesterday', 'last7', 'thismonth', 'lastmonth'):
        results = api.get_all_machines_total(period)
        print(format_report(results, period))
        print()


async def send_report():
    token_id, channel_id = getBotConfig()
    user, passwd, _ = getMatiPayCredentials()
    api = MatiPayAPI(user, passwd)

    periods = ['oggi', 'ultimi7gg']
    now = datetime.datetime.now()
    is_end_of_month = end_of_month(now.date())
    if is_end_of_month:
        periods.append('ultimi30gg')
        periods.append('questomese')

    from telegram import Bot
    from telegram.constants import ParseMode
    bot = Bot(token=token_id)

    # Send one text message per period
    messages = []
    for key in periods:
        period = telegramcmd[key]
        results = api.get_all_machines_total(period)
        msg = format_report(results, period)
        print(msg)
        messages.append(msg)
        await bot.send_message(chat_id=channel_id, text=msg)

        # Weekly message only: top selling slots + hourly distribution
        if key == 'ultimi7gg':
            per_slot, per_hour = api.get_slot_and_hourly(period)
            for block in format_top_slots(per_slot, top=10):
                print(block)
                await bot.send_message(chat_id=channel_id, text=block,
                                       parse_mode=ParseMode.MARKDOWN)
            hourly = format_hourly(per_hour)
            if hourly:
                print(hourly)
                await bot.send_message(chat_id=channel_id, text=hourly,
                                       parse_mode=ParseMode.MARKDOWN)

    # Week over week — only on last day of month
    if is_end_of_month:
        wow = api.get_week_over_week()
        msg = format_wow(wow)
        print(msg)
        await bot.send_message(chat_id=channel_id, text=msg)

    # 7-day graph
    breakdown = api.get_daily_breakdown(days=7)
    graph_7d = build_graph(breakdown)
    try:
        with open(graph_7d, 'rb') as f:
            await bot.send_photo(chat_id=channel_id, photo=f,
                                 caption='📈 Incasso — Ultimi 7 giorni')
    finally:
        os.remove(graph_7d)

    # Monthly trend graph — only on last day of month
    if is_end_of_month:
        trend = api.get_monthly_trend()
        graph_trend = build_monthly_trend_graph(trend)
        try:
            with open(graph_trend, 'rb') as f:
                await bot.send_photo(chat_id=channel_id, photo=f,
                                     caption='📊 Andamento mensile')
            # send_email(graph_trend, '\n\n'.join(messages))
        finally:
            os.remove(graph_trend)

    # Notifications / alarms
    notif_data = api.get_notifications()
    notif_msg = format_notifications(notif_data)
    print(notif_msg)
    await bot.send_message(chat_id=channel_id, text=notif_msg)


if __name__ == '__main__':
    import asyncio
    asyncio.run(send_report())
