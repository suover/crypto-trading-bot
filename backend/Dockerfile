FROM eclipse-temurin:21-jdk-alpine AS build
WORKDIR /build
COPY gradlew settings.gradle build.gradle ./
COPY gradle ./gradle
COPY src ./src
RUN sh ./gradlew --no-daemon bootJar

FROM eclipse-temurin:21-jre-alpine
RUN apk add --no-cache curl && addgroup -g 10001 app && adduser -D -u 10001 -G app app
WORKDIR /app
COPY --from=build /build/build/libs/*-0.1.0.jar /app/application.jar
USER 10001:10001
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=65", "-jar", "/app/application.jar"]
