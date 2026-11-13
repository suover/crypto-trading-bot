package io.github.suover.cryptotradingbot.tradeanalysis.service;

import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisResponses.*;
import java.math.BigDecimal;
import java.util.*;
import org.springframework.stereotype.Component;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@Component
public class StoredFeatureProjection {
    private static final JsonMapper JSON = JsonMapper.builder()
            .enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS)
            .enable(DeserializationFeature.USE_BIG_INTEGER_FOR_INTS).build();
    private static final List<String> FRAMES = List.of("15m", "1h", "4h", "1d");
    private static final List<String> METRICS = List.of("latest_price", "sma_5", "sma_20", "ema_5", "ema_20",
            "rsi_14", "recent_change_rate", "volume_ratio", "macd", "macd_signal", "macd_histogram",
            "atr_14", "atr_percentage", "realized_volatility", "max_drawdown");
    private static final Set<String> QUALITY = Set.of("SUFFICIENT", "INSUFFICIENT", "UNAVAILABLE");
    private static final Set<String> TREND = Set.of("상승 우위", "하락 우위", "관망", "판단 보류", "과열 주의", "과매도 주의");
    // Projection only: no indicators, eligibility, or trading decisions are recomputed.
    public FeatureSummary read(String raw) {
        if (raw == null || raw.length() > 1_000_000) return unavailable();
        try {
            JsonNode root = JSON.readTree(raw);
            if (!root.isObject()) return unavailable();
            String quality = enumValue(root.get("data_quality"), Set.of("SUFFICIENT", "PARTIAL", "INSUFFICIENT", "UNAVAILABLE"));
            JsonNode frames = root.get("timeframes");
            if (frames == null || !frames.isObject()) return unavailable();
            var result = new ArrayList<Timeframe>();
            boolean partial = quality == null;
            for (String key : FRAMES) {
                JsonNode frame = frames.get(key);
                if (frame == null) continue; // Missing configured frames are not fabricated.
                if (!frame.isObject()) {
                    partial = true;
                    result.add(new Timeframe(key, null, null, null, Map.of(), List.of("timeframe")));
                    continue;
                }
                var invalid = new ArrayList<String>();
                String q = enumValue(frame.get("data_quality"), QUALITY);
                if (q == null) invalid.add("data_quality");
                String trend = enumValue(frame.get("trend_label"), TREND);
                if (present(frame.get("trend_label")) && trend == null) invalid.add("trend_label");
                Integer count = null;
                JsonNode n = frame.get("candle_count");
                if (present(n)) {
                    if (n.isIntegralNumber() && n.canConvertToInt() && n.intValue() >= 0) count = n.intValue();
                    else invalid.add("candle_count");
                }
                var indicators = new LinkedHashMap<String, String>();
                for (String metric : METRICS) {
                    JsonNode value = frame.get(metric);
                    String parsed = numeric(value);
                    if (present(value) && parsed == null) invalid.add(metric);
                    indicators.put(metric, parsed);
                }
                partial |= !invalid.isEmpty();
                result.add(new Timeframe(key, q, count, trend,
                        Collections.unmodifiableMap(indicators), List.copyOf(invalid)));
            }
            if (result.isEmpty()) return unavailable();
            return new FeatureSummary(partial ? "PARTIAL" : "COMPLETE", quality, result);
        } catch (RuntimeException invalid) {
            return unavailable();
        }
    }
    private static boolean present(JsonNode n) { return n != null && !n.isNull(); }
    private static String enumValue(JsonNode node, Set<String> allowed) {
        return node != null && node.isString() && allowed.contains(node.asString()) ? node.asString() : null;
    }
    private static String numeric(JsonNode node) {
        if (!present(node) || !(node.isString() || node.isNumber())) return null;
        String text = node.isString() ? node.asString() : node.toString();
        if (text.length() > 128 || !text.matches("-?(0|[1-9]\\d*)(\\.\\d+)?([eE][+-]?\\d+)?")) return null;
        try {
            BigDecimal value = new BigDecimal(text);
            return value.precision() <= 80 && Math.abs((long) value.scale()) <= 100 ? value.toPlainString() : null;
        } catch (NumberFormatException invalid) { return null; }
    }
    private static FeatureSummary unavailable() {
        return new FeatureSummary("UNAVAILABLE", null, List.of());
    }
}
