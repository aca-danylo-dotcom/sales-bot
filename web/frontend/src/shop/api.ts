/**
 * Данные витрины: типы и запросы.
 *
 * Всё, что отдаёт web/api/shop.py. Считать здесь нечего: цены, скидки и
 * склонения приходят уже посчитанными и написанными по-русски — так же, как в
 * панели. Причина та же: «2 400 грн» в корзине и в сообщении бота должны
 * читаться одинаково, а формулу скидки незачем повторять на TypeScript.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { get, post, query } from "../api/client";

export type ShopInfo = {
  name: string;
  city: string;
  currency: string;
  delivery: string;
  payment: string;
  returns: string;
};

export type Profile = {
  name: string;
  phone: string;
  city: string;
  np_branch: string;
};

export type Promo = { code: string; percent: number };

export type Meta = {
  shop: ShopInfo;
  role: "admin" | "client";
  profile: Profile;
  cart_count: number;
  promo?: Promo;
};

export type ProductBrief = {
  id: number;
  title: string;
  category: string;
  price: number;
  price_text: string;
  old_price?: number | null;
  old_price_text?: string | null;
  in_stock: boolean;
  photo?: string | null;
};

export type Variant = {
  id: number;
  label: string;
  size: string;
  color: string;
  stock: number;
};

export type ProductFull = ProductBrief & {
  description: string;
  photos: string[];
  variants: Variant[];
};

export type CartItem = {
  variant_id: number;
  product_id: number;
  title: string;
  label: string;
  qty: number;
  stock: number;
  price: number;
  sum: number;
  sum_text: string;
};

export type Cart = {
  items: CartItem[];
  count: number;
  subtotal: number;
  subtotal_text: string;
  discount: number;
  discount_text?: string | null;
  total: number;
  total_text: string;
  promo?: Promo;
};

export type Order = {
  id: number;
  status: string;
  status_text: string;
  total: number;
  total_text: string;
  created_at: string;
  ttn?: string | null;
  items: { title: string; label: string; qty: number; sum_text: string }[];
};

export type CheckoutResult = {
  order_id: number;
  /** invoice — счёт уже в чате; card — туда же ушли реквизиты; none — бот молчит. */
  mode: "invoice" | "card" | "none";
  message: string;
};

/* Ключи запросов. Собраны в одном месте, чтобы правка корзины обновляла
   именно её, а не всё подряд: перечитывать витрину после «+1 шт» — лишняя
   секунда ожидания на телефоне. */
export const keys = {
  meta: ["shop", "meta"] as const,
  catalog: (q: string, category: string) => ["shop", "catalog", q, category] as const,
  product: (id: number) => ["shop", "product", id] as const,
  cart: ["shop", "cart"] as const,
  orders: ["shop", "orders"] as const,
};

/**
 * Кто открыл приложение и что показывать.
 *
 * `enabled` выключает запрос в обычном браузере: там подписи Telegram нет, и
 * сервер честно ответит 401 — панель на компьютере получила бы ошибку на
 * ровном месте.
 */
export function useShopMeta(enabled = true) {
  return useQuery({
    queryKey: keys.meta,
    queryFn: ({ signal }) => get<Meta>("/api/shop/meta", signal),
    staleTime: Infinity,
    enabled,
  });
}

export function useCatalog(search: string, category: string) {
  return useQuery({
    queryKey: keys.catalog(search, category),
    queryFn: ({ signal }) =>
      get<{ products: ProductBrief[]; categories: string[]; total: number }>(
        `/api/shop/catalog${query({ q: search, category })}`,
        signal,
      ),
  });
}

export function useProduct(id: number) {
  return useQuery({
    queryKey: keys.product(id),
    queryFn: ({ signal }) =>
      get<{ product: ProductFull }>(`/api/shop/products/${id}`, signal),
  });
}

export function useCart() {
  return useQuery({
    queryKey: keys.cart,
    queryFn: ({ signal }) => get<Cart>("/api/shop/cart", signal),
  });
}

export function useOrders() {
  return useQuery({
    queryKey: keys.orders,
    queryFn: ({ signal }) => get<{ orders: Order[] }>("/api/shop/orders", signal),
  });
}

/**
 * Действия с корзиной.
 *
 * Сервер отвечает уже пересчитанной корзиной, поэтому её кладём в кеш прямо
 * из ответа, а не перезапрашиваем: лишний круг по сети виден пальцем — счётчик
 * количества успевает мигнуть старым значением.
 */
export function useCartActions() {
  const client = useQueryClient();
  const store = (cart: Cart) => {
    client.setQueryData(keys.cart, cart);
    // Счётчик в шапке живёт в meta — держим его в согласии с корзиной.
    client.setQueryData<Meta>(keys.meta, (meta) =>
      meta ? { ...meta, cart_count: cart.count } : meta,
    );
  };

  const add = useMutation({
    mutationFn: (input: { variant_id: number; qty?: number }) =>
      post<Cart>("/api/shop/cart/add", { qty: 1, ...input }),
    onSuccess: store,
  });

  const setQty = useMutation({
    mutationFn: (input: { variant_id: number; qty: number }) =>
      post<Cart>("/api/shop/cart/qty", input),
    onSuccess: store,
  });

  const clear = useMutation({
    mutationFn: () => post<Cart>("/api/shop/cart/clear"),
    onSuccess: store,
  });

  const promo = useMutation({
    mutationFn: (code: string) => post<Cart>("/api/shop/promo", { code }),
    onSuccess: store,
  });

  return { add, setQty, clear, promo };
}

export function useCheckout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (fields: Profile & { comment?: string }) =>
      post<CheckoutResult>("/api/shop/checkout", fields),
    onSuccess: () => {
      // Заказ забрал корзину и, возможно, промокод — перечитываем и то и другое.
      client.invalidateQueries({ queryKey: keys.cart });
      client.invalidateQueries({ queryKey: keys.meta });
      client.invalidateQueries({ queryKey: keys.orders });
    },
  });
}
