FROM maven:3.9.9-eclipse-temurin-21 AS build
WORKDIR /build
COPY pom.xml .
RUN mvn -B dependency:go-offline
COPY src ./src
RUN mvn -B package -DskipTests

FROM eclipse-temurin:21-jre-alpine
RUN addgroup -g 10001 warehouse && adduser -D -u 10001 -G warehouse warehouse
WORKDIR /app
COPY --from=build /build/target/api-gateway.jar app.jar
USER warehouse
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
