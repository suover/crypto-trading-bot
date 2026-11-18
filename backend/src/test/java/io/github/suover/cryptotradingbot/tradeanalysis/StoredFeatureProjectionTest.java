package io.github.suover.cryptotradingbot.tradeanalysis;

import static org.assertj.core.api.Assertions.*;
import io.github.suover.cryptotradingbot.tradeanalysis.service.StoredFeatureProjection;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

class StoredFeatureProjectionTest {
    final StoredFeatureProjection projection = new StoredFeatureProjection();
    @Test void selectsOnlyKnownFieldsWithoutRecalculatingQualityOrLosingDigits() {
        var result = projection.read("""
                {"data_quality":"PARTIAL","position":{"secret":"not-exposed"},
                 "timeframes":{"15m":{"data_quality":"SUFFICIENT","candle_count":50,
                    "trend_label":"과열 주의","rsi_14":12.1234567890123456789012345678,
                    "latest_price":"12345678901234567890.1234567890",
                    "private":"not-exposed"},"1d":{"data_quality":"INSUFFICIENT","candle_count":3},
                    "5m":{"secret":"not-exposed"}}}
                """);
        assertThat(result.projectionStatus()).isEqualTo("COMPLETE");
        assertThat(result.dataQuality()).isEqualTo("PARTIAL");
        assertThat(result.timeframes()).extracting("timeframe").containsExactly("15m", "1d");
        assertThat(result.timeframes().getFirst().indicators().get("rsi_14"))
                .isEqualTo("12.1234567890123456789012345678");
        assertThat(result.timeframes().getFirst().indicators().get("latest_price"))
                .isEqualTo("12345678901234567890.1234567890");
        assertThat(result.timeframes().getFirst().trendLabel()).isEqualTo("과열 주의");
        assertThat(result.timeframes().getFirst().indicators()).doesNotContainKey("private");
        assertThat(result.timeframes().getLast().indicators().get("macd")).isNull();
    }
    @Test void readsEngineMinuteKeysInApiOrder() {
        var result = projection.read("""
                {"data_quality":"SUFFICIENT","timeframes":{
                    "1d":{"data_quality":"SUFFICIENT","rsi_14":"40.4"},
                    "240m":{"data_quality":"SUFFICIENT","rsi_14":"30.3"},
                    "15m":{"data_quality":"SUFFICIENT","rsi_14":"10.1"},
                    "60m":{"data_quality":"SUFFICIENT","rsi_14":"20.2"}}}
                """);
        assertThat(result.projectionStatus()).isEqualTo("COMPLETE");
        assertThat(result.timeframes()).extracting("timeframe").containsExactly("15m", "1h", "4h", "1d");
        assertThat(result.timeframes()).extracting(frame -> frame.indicators().get("rsi_14"))
                .containsExactly("10.1", "20.2", "30.3", "40.4");
    }
    @Test void keepsExistingHourKeysWhenBothSpellingsAreStored() {
        var result = projection.read("""
                {"data_quality":"SUFFICIENT","timeframes":{
                    "60m":{"data_quality":"SUFFICIENT","candle_count":60},
                    "1h":{"data_quality":"SUFFICIENT","candle_count":50},
                    "240m":{"data_quality":"SUFFICIENT","candle_count":40},
                    "4h":{"data_quality":"SUFFICIENT","candle_count":30}}}
                """);
        assertThat(result.timeframes()).extracting("timeframe").containsExactly("1h", "4h");
        assertThat(result.timeframes()).extracting("candleCount").containsExactly(50, 30);
    }
    @ParameterizedTest @ValueSource(strings = {"NaN", "Infinity", "-Infinity", "1e999999", "private-value", "01", "+1"})
    void invalidMetricIsNullWithExplicitProjectionQuality(String value) {
        var result = projection.read("{\"data_quality\":\"SUFFICIENT\",\"timeframes\":{\"15m\":"
                + "{\"data_quality\":\"SUFFICIENT\",\"rsi_14\":\"" + value + "\"}}}");
        assertThat(result.projectionStatus()).isEqualTo("PARTIAL");
        assertThat(result.dataQuality()).isEqualTo("SUFFICIENT");
        assertThat(result.timeframes().getFirst().indicators().get("rsi_14")).isNull();
        assertThat(result.timeframes().getFirst().invalidFields()).containsExactly("rsi_14");
    }
    @ParameterizedTest @NullAndEmptySource @ValueSource(strings = {"null", "[]", "{}", "{", "{\"timeframes\":[]}"})
    void absentOrMalformedFeaturesAreUnavailable(String raw) {
        assertThat(projection.read(raw).projectionStatus()).isEqualTo("UNAVAILABLE");
    }
    @Test void malformedFrameAndUnknownLabelsCannotLeakRawText() {
        var result = projection.read("""
                {"data_quality":"private","timeframes":{"15m":[],
                 "1h":{"data_quality":"private","trend_label":"private","candle_count":1.5}}}
                """);
        assertThat(result.projectionStatus()).isEqualTo("PARTIAL");
        assertThat(result.dataQuality()).isNull();
        assertThat(result.timeframes().getLast().trendLabel()).isNull();
        assertThat(result.timeframes().getLast().invalidFields()).contains("data_quality", "trend_label", "candle_count");
    }
    @Test void boundsPayloadBeforeParsing() {
        assertThat(projection.read(" ".repeat(1_000_001)).projectionStatus()).isEqualTo("UNAVAILABLE");
    }
}
