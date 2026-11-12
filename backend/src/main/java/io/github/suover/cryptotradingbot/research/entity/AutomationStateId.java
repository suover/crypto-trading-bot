package io.github.suover.cryptotradingbot.research.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import java.io.Serializable;
import java.util.Objects;

@Embeddable
public class AutomationStateId implements Serializable {
    @Column(name = "user_id") private Long userId;
    private String exchange;
    @Column(name = "quote_asset") private String quoteAsset;
    @Column(name = "dataset_schema_version") private String datasetSchemaVersion;
    @Column(name = "baseline_policy_signature") private String baselinePolicySignature;
    @Column(name = "effective_top_n") private Integer effectiveTopN;
    protected AutomationStateId() {}
    public Long getUserId() { return userId; }
    public String getExchange() { return exchange; }
    public String getQuoteAsset() { return quoteAsset; }
    public String getDatasetSchemaVersion() { return datasetSchemaVersion; }
    public String getBaselinePolicySignature() { return baselinePolicySignature; }
    public Integer getEffectiveTopN() { return effectiveTopN; }
    @Override public boolean equals(Object other) {
        if (this == other) return true;
        if (!(other instanceof AutomationStateId that)) return false;
        return Objects.equals(userId, that.userId) && Objects.equals(exchange, that.exchange)
                && Objects.equals(quoteAsset, that.quoteAsset)
                && Objects.equals(datasetSchemaVersion, that.datasetSchemaVersion)
                && Objects.equals(baselinePolicySignature, that.baselinePolicySignature)
                && Objects.equals(effectiveTopN, that.effectiveTopN);
    }
    @Override public int hashCode() {
        return Objects.hash(userId, exchange, quoteAsset, datasetSchemaVersion,
                baselinePolicySignature, effectiveTopN);
    }
}
