package io.github.suover.cryptotradingbot.overview.controller;

import io.github.suover.cryptotradingbot.overview.dto.OverviewQuery;
import io.github.suover.cryptotradingbot.overview.dto.OverviewResponses.*;
import io.github.suover.cryptotradingbot.overview.service.OverviewReadService;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.Set;
import org.springframework.util.MultiValueMap;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/overview")
public class OverviewController {
    private final OverviewReadService service;
    public OverviewController(OverviewReadService service) { this.service = service; }
    @GetMapping("/summary")
    public Summary summary(@RequestParam MultiValueMap<String, String> parameters) {
        allowed(parameters, Set.of());
        return service.summary();
    }
    @GetMapping("/positions")
    public Positions positions(@RequestParam MultiValueMap<String, String> parameters) {
        allowed(parameters, Set.of());
        return service.positions();
    }
    @GetMapping("/performance")
    public Page<Performance> performance(@RequestParam MultiValueMap<String, String> parameters) {
        allowed(parameters, Set.of("from", "to", "page", "size"));
        return service.performance(new OverviewQuery.Performance(instant(parameters.getFirst("from")),
                instant(parameters.getFirst("to")), number(parameters, "page", 0), number(parameters, "size", 90)));
    }
    @GetMapping("/recommendations")
    public Page<Recommendation> recommendations(@RequestParam MultiValueMap<String, String> parameters) {
        allowed(parameters, Set.of("action", "page", "size"));
        return service.recommendations(new OverviewQuery.Recommendations(parameters.getFirst("action"),
                number(parameters, "page", 0), number(parameters, "size", 10)));
    }
    private static void allowed(MultiValueMap<String, String> parameters, Set<String> keys) {
        if (!keys.containsAll(parameters.keySet()) || parameters.values().stream().anyMatch(v -> v.size() != 1)) {
            throw new IllegalArgumentException("Unsupported or repeated query parameter");
        }
    }
    private static int number(MultiValueMap<String, String> p, String key, int fallback) {
        return p.containsKey(key) ? Integer.parseInt(p.getFirst(key)) : fallback;
    }
    private static Instant instant(String value) {
        try { return value == null ? null : Instant.parse(value); }
        catch (DateTimeParseException invalid) { throw new IllegalArgumentException("Invalid timestamp"); }
    }
}
