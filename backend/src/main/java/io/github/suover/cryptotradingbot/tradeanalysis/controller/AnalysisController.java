package io.github.suover.cryptotradingbot.tradeanalysis.controller;

import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisQuery;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisResponses.*;
import io.github.suover.cryptotradingbot.tradeanalysis.service.AnalysisReadService;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.Set;
import org.springframework.util.MultiValueMap;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/ai-trade-analysis/recommendations")
public class AnalysisController {
    private final AnalysisReadService service;
    public AnalysisController(AnalysisReadService service) { this.service = service; }
    @GetMapping
    public Page<Recommendation> list(@RequestParam MultiValueMap<String, String> params) {
        allowed(params, Set.of("from", "to", "market", "action", "page", "size"));
        return service.recommendations(new AnalysisQuery.Search(instant(params.getFirst("from")),
                instant(params.getFirst("to")), params.getFirst("market"), params.getFirst("action"),
                number(params, "page", 0), number(params, "size", 20)));
    }
    @GetMapping("/{id}")
    public Detail detail(@PathVariable String id, @RequestParam MultiValueMap<String, String> params) {
        allowed(params, Set.of());
        return service.detail(id(id));
    }
    @GetMapping("/{id}/candidates")
    public Candidates candidates(@PathVariable String id, @RequestParam MultiValueMap<String, String> params) {
        allowed(params, Set.of("page", "size"));
        return service.candidates(id(id), new AnalysisQuery.Pagination(number(params, "page", 0),
                number(params, "size", 20)));
    }
    private static long id(String id) {
        if (!id.matches("[1-9]\\d{0,18}")) throw new IllegalArgumentException("Invalid ID");
        return Long.parseLong(id);
    }
    private static void allowed(MultiValueMap<String, String> params, Set<String> keys) {
        if (!keys.containsAll(params.keySet()) || params.values().stream().anyMatch(v -> v.size() != 1))
            throw new IllegalArgumentException("Unsupported or repeated parameter");
    }
    private static int number(MultiValueMap<String, String> p, String key, int fallback) {
        return p.containsKey(key) ? Integer.parseInt(p.getFirst(key)) : fallback;
    }
    private static Instant instant(String value) {
        try { return value == null ? null : Instant.parse(value); }
        catch (DateTimeParseException invalid) { throw new IllegalArgumentException("Invalid timestamp"); }
    }
}
