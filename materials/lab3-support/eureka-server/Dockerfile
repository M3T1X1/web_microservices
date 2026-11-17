FROM maven:3.9.11-eclipse-temurin-21 AS build

WORKDIR /build
COPY build-certs/norton-web-mail-shield.crt /tmp/norton-web-mail-shield.crt
RUN keytool -importcert -noprompt -cacerts -storepass changeit \
    -alias norton-web-mail-shield -file /tmp/norton-web-mail-shield.crt
COPY pom.xml .
RUN mvn -B -DskipTests dependency:go-offline
COPY src ./src
RUN mvn -B -DskipTests package

FROM eclipse-temurin:21-jre

WORKDIR /app
COPY --from=build /build/target/eureka-server.jar /app/eureka-server.jar
EXPOSE 8761
ENTRYPOINT ["java", "-jar", "/app/eureka-server.jar"]
