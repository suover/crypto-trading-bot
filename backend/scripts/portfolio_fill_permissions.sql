GRANT SELECT (id, order_log_id, price, volume, funds_krw, created_at)
    ON public.order_fills TO backend_reader;
