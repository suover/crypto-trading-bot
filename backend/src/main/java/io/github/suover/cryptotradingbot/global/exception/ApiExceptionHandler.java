package io.github.suover.cryptotradingbot.global.exception;

import io.github.suover.cryptotradingbot.research.service.ResearchNotFoundException;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import jakarta.validation.ConstraintViolationException;

@RestControllerAdvice
public class ApiExceptionHandler {
    @ExceptionHandler({IllegalArgumentException.class, MethodArgumentTypeMismatchException.class,
            MissingServletRequestParameterException.class, MethodArgumentNotValidException.class,
            ConstraintViolationException.class, HandlerMethodValidationException.class})
    ResponseEntity<ApiError> badRequest(Exception exception) {
        return ResponseEntity.badRequest().body(ApiError.of("INVALID_REQUEST", "Invalid request parameters"));
    }
    @ExceptionHandler(ResearchNotFoundException.class)
    ResponseEntity<ApiError> notFound() {
        return ResponseEntity.status(404).body(ApiError.of("NOT_FOUND", "Research candidate not found"));
    }
    @ExceptionHandler(AccessDeniedException.class)
    ResponseEntity<ApiError> forbidden() {
        return ResponseEntity.status(403).body(ApiError.of("FORBIDDEN", "Access denied"));
    }
    @ExceptionHandler(Exception.class)
    ResponseEntity<ApiError> internal(Exception exception) {
        // Do not serialize JDBC messages, query parameters, credentials or stack traces.
        return ResponseEntity.internalServerError().body(ApiError.of("INTERNAL_ERROR", "Request could not be completed"));
    }
}
