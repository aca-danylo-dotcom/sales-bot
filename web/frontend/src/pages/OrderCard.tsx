/**
 * Карточка заказа — место, где с заказом что-то происходит.
 *
 * Каждая кнопка здесь не только меняет статус, но и пишет клиенту в Telegram:
 * подтверждение оплаты, номер накладной, отмену. Поэтому ответ сервера
 * показываем целиком — вместе с оговоркой «сообщение не дошло», если бот
 * заблокирован. И поэтому же все четыре срабатывают по удержанию, а не по
 * нажатию (см. components/ui/hold-button.tsx): отменённый по промаху заказ
 * уже не вернуть — клиенту ушло сообщение, товар уехал обратно на склад. Что именно можно нажать, решает сервер (`can_confirm`,
 * `can_ship`, `can_finish`, `can_cancel`): состояние заказа знает он, а не
 * страница, открытая полчаса назад.
 *
 * Вкладка «Клиент» — всё про человека: прошлые заказы, память бота и переписка.
 * Данные для неё грузятся только когда вкладку открыли (`?tab=client`).
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { CheckCircle2, PackageCheck, Truck, XCircle } from "lucide-react";

import { get } from "../api/client";
import { BackLink, Head, Loading, LoadError, Tag, stamp } from "../components/ui";
import HoldButton from "../components/ui/hold-button";
import { useAction } from "../lib/actions";
import { usePageTitle } from "../lib/meta";

type Item = {
  id: number;
  title_snapshot: string;
  photo_id: number | null;
  qty: number;
  variant: string;
  price_text: string;
  sum_text: string;
  stock_now: number | null;
};

type Order = {
  id: number;
  client_id: number;
  channel: string;
  status: string;
  status_short: string;
  status_name: string;
  created_at: string;
  name: string | null;
  phone: string | null;
  city: string | null;
  np_branch: string | null;
  comment: string | null;
  note: string | null;
  ttn: string | null;
  assignee: string | null;
  total_text: string;
  /* Пусто, когда промокода не было. Сумма до скидки приходит отдельным полем:
     считать её на клиенте нельзя — деньги в панели и в счёте клиента должны
     совпадать до копейки, а округление у них разное не будет только если
     считает кто-то один. */
  discount_text: string;
  full_text: string;
  promo_code: string | null;
  items: Item[];
  client: { name: string | null; phone: string | null; created_at: string | null } | null;
};

type ClientNote = { id: number; fact: string; created_at: string };
type Promo = {
  code: string;
  percent: number;
  expires_at: string;
  activated_at: string | null;
  used_at: string | null;
};
type ChatLine = { role: string; content: string; created_at: string };
type OtherOrder = {
  id: number;
  created_at: string;
  total_text: string;
  status: string;
  status_short: string;
};

type Card = {
  order: Order;
  manager: string;
  can_confirm: boolean;
  can_ship: boolean;
  can_finish: boolean;
  can_cancel: boolean;
  timeline: { title: string; stamp: string | null }[];
  client_orders?: OtherOrder[];
  history?: ChatLine[];
  client_notes?: ClientNote[];
  client_email?: string;
  client_promos?: Promo[];
};

export default function OrderCard() {
  const { id } = useParams();
  usePageTitle(`Заказ №${id}`);

  const [params] = useSearchParams();
  const tab = params.get("tab") === "client" ? "client" : "order";

  // Фильтры списка приехали в адресе карточки — по ним и возвращаемся назад,
  // в тот же отфильтрованный список, а не в его начало.
  const back = new URLSearchParams(params);
  back.delete("tab");
  const backQs = back.toString();
  const tabHref = (value: "order" | "client") => {
    const next = new URLSearchParams(back);
    if (value === "client") next.set("tab", "client");
    const text = next.toString();
    return `/orders/${id}${text ? `?${text}` : ""}`;
  };

  const cardKey = ["order", id, tab];
  const { data, error, isPending, refetch } = useQuery({
    queryKey: cardKey,
    queryFn: ({ signal }) =>
      get<Card>(`/api/orders/${id}${tab === "client" ? "?tab=client" : ""}`, signal),
  });

  // Любое действие перечитывает и карточку (обе вкладки), и списки: цифры на
  // вкладках «Заказов» и сводка после подтверждения оплаты меняются тоже.
  const invalidate = [["order", id], ["orders"], ["summary"]];
  const status = useAction({ invalidate });
  const takeAction = useAction({ invalidate });
  const shipAction = useAction({ invalidate });
  const cancelAction = useAction({ invalidate });
  const contactsAction = useAction({ invalidate });
  const noteAction = useAction({ invalidate });
  const forgetAction = useAction({ invalidate });

  const order = data?.order;

  const [manager, setManager] = useState("");
  const [ttn, setTtn] = useState("");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [contacts, setContacts] = useState({
    name: "",
    phone: "",
    city: "",
    np_branch: "",
    comment: "",
  });

  // Поля подтягиваются за сервером: после сохранения данные перечитываются, и
  // в форме должно оказаться то, что действительно записано.
  useEffect(() => {
    if (!data) return;
    setManager(data.manager);
    setTtn(data.order.ttn ?? "");
    setNote(data.order.note ?? "");
    setContacts({
      name: data.order.name ?? "",
      phone: data.order.phone ?? "",
      city: data.order.city ?? "",
      np_branch: data.order.np_branch ?? "",
      comment: data.order.comment ?? "",
    });
  }, [data]);

  if (isPending) return <Loading />;
  if (error || !order) return <LoadError error={error} onRetry={() => refetch()} />;

  const url = (action: string) => `/api/orders/${order.id}/${action}`;

  return (
    <>
      <BackLink to={`/orders${backQs ? `?${backQs}` : ""}`}>← Все заказы</BackLink>

      <Head
        title={
          <>
            Заказ №{order.id} <Tag kind={`st-${order.status}`}>{order.status_short}</Tag>
          </>
        }
        lead={`${order.status_name} · оформлен ${stamp(order.created_at)}`}
      >
        {/* «Взять в работу» — единственное место, где менеджер называет себя:
            входа в панель нет, а знать, кто ведёт заказ, надо. Имя запомнит
            браузер. */}
        {order.assignee ? (
          <>
            <Tag kind="on">Ведёт: {order.assignee}</Tag>
            <button
              className="btn ghost"
              type="button"
              disabled={takeAction.isPending}
              onClick={() => takeAction.mutate({ url: url("release") })}
            >
              Отпустить
            </button>
          </>
        ) : (
          <form
            className="take"
            onSubmit={(event) => {
              event.preventDefault();
              takeAction.mutate({ url: url("take"), data: { manager } });
            }}
          >
            <input
              type="text"
              value={manager}
              placeholder="Ваше имя"
              autoComplete="off"
              onChange={(event) => setManager(event.target.value)}
            />
            <button className="btn primary" type="submit" disabled={takeAction.isPending}>
              Взять в работу
            </button>
          </form>
        )}
      </Head>

      <nav className="tabs">
        <Link className={`tab ${tab === "order" ? "active" : ""}`} to={tabHref("order")}>
          Заказ
        </Link>
        <Link className={`tab ${tab === "client" ? "active" : ""}`} to={tabHref("client")}>
          Клиент
        </Link>
      </nav>

      {tab === "order" ? (
        <div className="order-grid">
          <div>
            <div className="card">
              <h2>Что отправляем</h2>
              <ul className="items">
                {order.items.map((item) => (
                  <li key={item.id}>
                    {item.photo_id ? (
                      <img className="thumb" src={`/media/${item.photo_id}`} alt="" loading="lazy" />
                    ) : (
                      <span className="thumb empty">—</span>
                    )}
                    <div className="item-main">
                      <span className="strong">{item.title_snapshot}</span>
                      <span className="muted small">
                        {item.variant}
                        {item.stock_now !== null ? ` · на складе сейчас ${item.stock_now}` : ""}
                      </span>
                    </div>
                    <div className="item-sum num">
                      {item.qty} × {item.price_text}
                      <span className="strong">{item.sum_text}</span>
                    </div>
                  </li>
                ))}
              </ul>
              {order.discount_text ? (
                <p className="muted small">
                  Сумма без скидки: {order.full_text} · промокод {order.promo_code} — минус{" "}
                  {order.discount_text}
                </p>
              ) : null}
              <p className="total">
                Итого: <span className="strong">{order.total_text}</span>
              </p>
              {order.comment ? (
                <p className="muted">Комментарий клиента: {order.comment}</p>
              ) : null}
            </div>

            <div className="card">
              <h2>Что делаем</h2>
              {/* Действия, меняющие статус заказа, срабатывают не по нажатию, а
                  по удержанию — кнопка «Hold Button» из реестра kokonutui,
                  владелец прислал её именно под это. Причина не в красоте:
                  каждое из них уходит клиенту сообщением, а отмена ещё и
                  возвращает товар на склад. Промахнуться мимо такого нельзя, а
                  окно «Вы уверены?» на десятом заказе за день просто
                  прокликивают не глядя. */}
              <div className="actions hold-actions">
                {data.can_confirm ? (
                  <HoldButton
                    variant="green"
                    label="Оплата пришла"
                    icon={<CheckCircle2 className="h-4 w-4" />}
                    disabled={status.isPending}
                    onHoldComplete={() => status.mutate({ url: url("confirm") })}
                  />
                ) : null}
                {data.can_finish ? (
                  <HoldButton
                    variant="grey"
                    label="Заказ выполнен"
                    icon={<PackageCheck className="h-4 w-4" />}
                    disabled={status.isPending}
                    onHoldComplete={() => status.mutate({ url: url("done") })}
                  />
                ) : null}
              </div>
              {data.can_confirm || data.can_finish ? (
                <p className="muted small hold-hint">
                  Нажмите и удерживайте кнопку, пока полоса не заполнится.
                </p>
              ) : null}

              {/* Пока заказ не взят, поле накладной не показываем: отправку
                  подписывают именем, и заполнять номер, который всё равно не
                  сохранится, — зря. */}
              {data.can_ship && !order.assignee ? (
                <p className="muted">
                  Чтобы отметить отправку, сначала возьмите заказ в работу — имя вписывается
                  вверху страницы.
                </p>
              ) : data.can_ship ? (
                <form className="form ttn" onSubmit={(event) => event.preventDefault()}>
                  {/* Отправка формы гасится и ничего не делает: Enter в поле
                      номера обошёл бы удержание кнопки — заказ уехал бы
                      клиенту с полунабранной накладной. Действие тут ровно
                      одно — то, что под пальцем. Так же и в форме отмены. */}
                  <label>
                    <span>Номер накладной Новой Почты</span>
                    <input
                      type="text"
                      value={ttn}
                      autoComplete="off"
                      placeholder="59000000000000"
                      onChange={(event) => setTtn(event.target.value)}
                    />
                  </label>
                  <div className="actions hold-actions">
                    <HoldButton
                      variant="blue"
                      label="Отправлено, сообщить клиенту"
                      icon={<Truck className="h-4 w-4" />}
                      disabled={shipAction.isPending}
                      onHoldComplete={() => shipAction.mutate({ url: url("ship"), data: { ttn } })}
                    />
                    <span className="muted small">
                      Отправку записываем на вас: {order.assignee}. Клиенту уйдёт сообщение с
                      номером накладной.
                    </span>
                  </div>
                </form>
              ) : order.ttn ? (
                <p className="muted">Накладная: {order.ttn}</p>
              ) : null}

              {data.can_cancel ? (
                <form className="form cancel" onSubmit={(event) => event.preventDefault()}>
                  <label>
                    <span>
                      Отмена — товар вернётся на склад. Причина останется в заказе, клиенту не
                      уйдёт
                    </span>
                    <input
                      type="text"
                      value={reason}
                      autoComplete="off"
                      placeholder="Например: не дозвонились, клиент передумал"
                      onChange={(event) => setReason(event.target.value)}
                    />
                  </label>
                  <div className="actions hold-actions">
                    <HoldButton
                      variant="red"
                      label="Отменить заказ"
                      icon={<XCircle className="h-4 w-4" />}
                      disabled={cancelAction.isPending}
                      onHoldComplete={() =>
                        cancelAction.mutate({ url: url("cancel"), data: { reason } })
                      }
                    />
                  </div>
                </form>
              ) : null}
            </div>
          </div>

          <div>
            <div className="card">
              <h2>Куда отправляем</h2>
              <form
                className="form"
                onSubmit={(event) => {
                  event.preventDefault();
                  contactsAction.mutate({ url: url("contacts"), data: contacts });
                }}
              >
                {(
                  [
                    ["name", "Получатель"],
                    ["phone", "Телефон"],
                    ["city", "Город"],
                    ["np_branch", "Отделение Новой Почты"],
                    ["comment", "Комментарий"],
                  ] as const
                ).map(([field, title]) => (
                  <label key={field}>
                    <span>{title}</span>
                    <input
                      type="text"
                      value={contacts[field]}
                      onChange={(event) =>
                        setContacts({ ...contacts, [field]: event.target.value })
                      }
                    />
                  </label>
                ))}
                <div className="actions">
                  <button className="btn" type="submit" disabled={contactsAction.isPending}>
                    Сохранить
                  </button>
                </div>
              </form>
            </div>

            <div className="card">
              <h2>Заметка для своих</h2>
              <form
                className="form"
                onSubmit={(event) => {
                  event.preventDefault();
                  noteAction.mutate({ url: url("note"), data: { note } });
                }}
              >
                <textarea
                  rows={3}
                  value={note}
                  placeholder="Клиент не видит"
                  onChange={(event) => setNote(event.target.value)}
                />
                <div className="actions">
                  <button className="btn" type="submit" disabled={noteAction.isPending}>
                    Сохранить
                  </button>
                </div>
              </form>
            </div>

            <div className="card">
              <h2>Как шло</h2>
              <ol className="timeline">
                {data.timeline.map((step) => (
                  <li key={step.title} className={step.stamp ? "done" : ""}>
                    <span>{step.title}</span>
                    <span className="muted small">{step.stamp ? stamp(step.stamp) : "—"}</span>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="card">
            <h2>Клиент</h2>
            <p className="pairs">
              <span className="muted small">Имя в профиле</span> {order.client?.name || "—"}
              <br />
              <span className="muted small">Телефон</span> {order.client?.phone || "—"}
              <br />
              {/* Почта есть далеко не у всех: её оставляют по желанию, и «не
                  указана» здесь — обычное дело, а не пробел в данных. */}
              <span className="muted small">Почта</span>{" "}
              {data.client_email || "не указана"}
              <br />
              <span className="muted small">Telegram id</span> {order.client_id}
              <br />
              <span className="muted small">Канал</span> {order.channel}
              {order.client?.created_at ? (
                <>
                  <br />
                  <span className="muted small">С нами с</span>{" "}
                  {order.client.created_at.slice(0, 10)}
                </>
              ) : null}
            </p>
          </div>

          {/* Промокоды этого человека. Блок нужен на один частый разговор:
              «мне присылали скидку» — видно, какой код, жив ли он и не потрачен
              ли уже на прошлый заказ. */}
          {data.client_promos?.length ? (
            <div className="card">
              <h2>Промокоды</h2>
              <ul className="notes">
                {data.client_promos.map((promo) => (
                  <li key={promo.code}>
                    <span className="text">
                      <span className="strong">{promo.code}</span> · −{promo.percent}%
                      <span className="muted small">
                        {" "}
                        {promo.used_at
                          ? `потрачен ${stamp(promo.used_at)}`
                          : promo.expires_at < new Date().toISOString().slice(0, 19).replace("T", " ")
                            ? "срок вышел"
                            : promo.activated_at
                              ? "принят клиентом, ждёт заказа"
                              : `выписан, действует до ${promo.expires_at.slice(0, 10)}`}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="card">
            <h2>Другие заказы</h2>
            {data.client_orders?.length ? (
              <table className="data-grid">
                <thead>
                  <tr>
                    <th>Заказ</th>
                    <th>Дата</th>
                    <th className="num">Сумма</th>
                    <th>Статус</th>
                  </tr>
                </thead>
                <tbody>
                  {data.client_orders.map((other) => (
                    <tr key={other.id}>
                      <td>
                        <Link className="strong" to={`/orders/${other.id}`}>
                          №{other.id}
                        </Link>
                      </td>
                      <td className="muted">{stamp(other.created_at)}</td>
                      <td className="num">{other.total_text}</td>
                      <td>
                        <Tag kind={`st-${other.status}`}>{other.status_short}</Tag>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">Этот заказ у клиента первый.</p>
            )}
          </div>

          <div className="card">
            <h2>Что бот помнит о клиенте</h2>
            {/* Эти строки уходят боту в промпт на каждом сообщении — по ним он
                и советует. Понял человека не так («берёт мужу», а на самом деле
                себе) — удаляем, и в следующем разговоре этого факта у него уже
                нет. */}
            {data.client_notes?.length ? (
              <>
                <ul className="notes">
                  {data.client_notes.map((fact) => (
                    <li key={fact.id}>
                      <span className="text">{fact.fact}</span>
                      <span className="muted small">{fact.created_at.slice(0, 10)}</span>
                      <button
                        className="btn ghost"
                        type="button"
                        disabled={forgetAction.isPending}
                        onClick={() =>
                          forgetAction.mutate({
                            url: url("client-note-delete"),
                            data: { note_id: fact.id },
                          })
                        }
                      >
                        Забыть
                      </button>
                    </li>
                  ))}
                </ul>
                <p className="muted small">
                  Бот записывает такие факты сам, когда клиент о себе рассказывает. Личные
                  данные — телефон, адрес, карта — сюда не попадают.
                </p>
              </>
            ) : (
              <p className="muted">Бот пока ничего о клиенте не запомнил.</p>
            )}
          </div>

          <div className="card">
            <h2>Переписка с ботом</h2>
            {/* Последние реплики — чтобы не выяснять у клиента заново то, что он
                уже рассказал боту: какой размер мерил, о какой скидке спрашивал. */}
            {data.history?.length ? (
              <ul className="chat">
                {data.history.map((line, index) => (
                  <li
                    key={`${line.created_at}-${index}`}
                    className={line.role === "user" ? "from-client" : "from-bot"}
                  >
                    <span className="who">{line.role === "user" ? "Клиент" : "Бот"}</span>
                    <span className="text">{line.content}</span>
                    <span className="muted small">{stamp(line.created_at)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">Переписки пока нет.</p>
            )}
          </div>
        </>
      )}
    </>
  );
}
