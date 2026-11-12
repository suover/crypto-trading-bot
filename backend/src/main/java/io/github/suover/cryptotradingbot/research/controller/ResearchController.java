package io.github.suover.cryptotradingbot.research.controller;

import io.github.suover.cryptotradingbot.research.dto.CandidateSearch;
import io.github.suover.cryptotradingbot.research.dto.ResearchResponses.*;
import io.github.suover.cryptotradingbot.research.service.ResearchReadService;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.time.Instant;
import java.util.Map;
import java.util.Set;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/research")
public class ResearchController {
    private final ResearchReadService service;
    public ResearchController(ResearchReadService service) { this.service = service; }

    @GetMapping("/summary")
    public Summary summary(@RequestParam Map<String, String> parameters) {
        allowed(parameters, Set.of());
        return service.summary();
    }
    @GetMapping("/candidates")
    public Page<CandidateItem> candidates(
            @RequestParam(required = false) @Size(max = 80) String keyword,
            @RequestParam(required = false) Instant registeredFrom,
            @RequestParam(required = false) Instant registeredTo,
            @RequestParam(required = false) Boolean shadowEnrolled,
            @RequestParam(defaultValue = "0") @Min(0) int page,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size,
            @RequestParam(defaultValue = "registeredAt,desc") String sort,
            @RequestParam Map<String, String> parameters) {
        allowed(parameters, Set.of("keyword", "registeredFrom", "registeredTo", "shadowEnrolled",
                "page", "size", "sort"));
        return service.candidates(new CandidateSearch(keyword, registeredFrom, registeredTo,
                shadowEnrolled, page, size, sort));
    }
    @GetMapping("/candidates/{id}")
    public CandidateDetail candidate(@PathVariable @Positive long id, @RequestParam Map<String, String> parameters) {
        allowed(parameters, Set.of());
        return service.candidate(id);
    }
    @GetMapping("/snapshots")
    public Page<SnapshotItem> snapshots(@RequestParam(defaultValue = "0") @Min(0) int page,
                                        @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size,
                                        @RequestParam Map<String, String> parameters) {
        allowed(parameters, Set.of("page", "size"));
        CandidateSearch.validatePage(page, size);
        return service.snapshots(page, size);
    }
    @GetMapping("/automation-status")
    public AutomationStatus automationStatus(@RequestParam Map<String, String> parameters) {
        allowed(parameters, Set.of());
        return service.automationStatus();
    }
    private static void allowed(Map<String, String> parameters, Set<String> keys) {
        if (!keys.containsAll(parameters.keySet())) throw new IllegalArgumentException("Unsupported query parameter");
    }
}
