// Container Apps Environment + Backend + Frontend Container Apps
param location string
param resourceToken string
param containerAppName string
param frontendContainerAppName string
param tags object
param serviceResourceTags object

// Monitoring
param logAnalyticsCustomerId string
@secure()
param logAnalyticsSharedKey string
param applicationInsightsConnectionString string

// Identity
param userManagedIdentityId string
param userManagedIdentityClientId string

// Container Registry
param containerRegistryLoginServer string

// Storage
param storageAccountName string
param blobEndpoint string
param containerName string

// Cosmos DB
param cosmosEndpoint string
param cosmosDatabaseName string
param cosmosContainerName string
param cosmosConfigContainerName string

// Document Intelligence
param documentIntelligenceEndpoint string

// AI Services
param aiServicesEndpoint string
param foundryProjectEndpoint string
param azureOpenaiModelDeploymentName string

// Content Understanding + cost/preprocessing features
param contentUnderstandingEndpoint string = ''
@allowed([
  'gpt'
  'content_understanding'
])
param extractionBackend string = 'gpt'
param enableImagePreprocessing bool = false
param summaryModelDeploymentName string = ''

@description('Default extraction tier when a dataset or request does not specify one')
@allowed([
  'economy'
  'standard'
  'premium'
])
param defaultExtractionTier string = 'standard'

@description('Whether pricing should use Azure Retail Prices API before fallback prices')
param pricingUseRetailApi bool = true

@description('Fraction of low-quality pages that routes a document to review')
param routingLowQualityPageFraction string = '0.5'

@description('Minimum low-quality page count that routes a document to review')
param routingLowQualityMinPages string = '1'

@description('Minimum OCR character count before OCR is considered unreadable')
param routingMinOcrTextLength string = '20'

@description('Maximum page count eligible for economy tier auto-routing')
param routingEconomyMaxPages string = '1'

@description('Content Understanding default completion model deployment name')
param contentUnderstandingCompletionModel string = ''

@description('Content Understanding default embedding model deployment name')
param contentUnderstandingEmbeddingModel string = ''

@description('PaddleOCR quality-probe container image')
param paddleOcrImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Enable the PaddleOCR pre-gate (cost-saving quality short-circuit before DI/CU)')
param enablePaddlePregate bool = false

@description('PaddleOCR pre-gate behavior on a low-quality verdict')
@allowed([
  'block'
  'advisory'
])
param paddlePregateMode string = 'block'

@description('Flag if mean PaddleOCR line confidence is below this')
param paddleConfidenceMeanMin string = '0.80'

@description('Flag if the fraction of low-confidence PaddleOCR lines exceeds this')
param paddleConfidenceLowFracMax string = '0.25'

@description('A PaddleOCR line below this score counts as low confidence')
param paddleConfidenceWordMin string = '0.70'

// Key Vault
param keyVaultUri string

// API Key (Key Vault secret URI)
param apiKeySecretUri string

// VNet
param containerAppsSubnetId string

resource containerAppEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${resourceToken}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: containerAppsSubnetId
      internal: false
    }
  }
  tags: tags
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userManagedIdentityId}': {}
    }
  }
  properties: {
    environmentId: containerAppEnvironment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        corsPolicy: {
          allowedOrigins: ['*']
          allowedMethods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
          allowedHeaders: ['*']
          maxAge: 3600
        }
      }
      secrets: [
        {
          name: 'api-key'
          keyVaultUrl: apiKeySecretUri
          identity: userManagedIdentityId
        }
      ]
      registries: [
        {
          server: containerRegistryLoginServer
          identity: userManagedIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: containerAppName
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            { name: 'API_KEY', secretRef: 'api-key' }
            { name: 'STORAGE_ACCOUNT_NAME', value: storageAccountName }
            { name: 'BLOB_ACCOUNT_URL', value: blobEndpoint }
            { name: 'CONTAINER_NAME', value: containerName }
            { name: 'COSMOS_URL', value: cosmosEndpoint }
            { name: 'COSMOS_DB_NAME', value: cosmosDatabaseName }
            { name: 'COSMOS_DOCUMENTS_CONTAINER_NAME', value: cosmosContainerName }
            { name: 'COSMOS_CONFIG_CONTAINER_NAME', value: cosmosConfigContainerName }
            { name: 'DOCUMENT_INTELLIGENCE_ENDPOINT', value: documentIntelligenceEndpoint }
            { name: 'AZURE_OPENAI_ENDPOINT', value: aiServicesEndpoint }
            { name: 'AZURE_AI_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            { name: 'AZURE_OPENAI_MODEL_DEPLOYMENT_NAME', value: azureOpenaiModelDeploymentName }
            // Content Understanding extraction backend
            { name: 'AZURE_LOCATION', value: location }
            { name: 'AZURE_CONTENT_UNDERSTANDING_ENDPOINT', value: contentUnderstandingEndpoint }
            { name: 'EXTRACTION_BACKEND', value: extractionBackend }
            { name: 'CONTENT_UNDERSTANDING_COMPLETION_MODEL', value: contentUnderstandingCompletionModel }
            { name: 'CONTENT_UNDERSTANDING_EMBEDDING_MODEL', value: contentUnderstandingEmbeddingModel }
            // PaddleOCR cost-saving pre-gate (self-hosted quality probe before DI/CU)
            { name: 'ENABLE_PADDLE_PREGATE', value: toLower(string(enablePaddlePregate)) }
            { name: 'PADDLE_PREGATE_MODE', value: paddlePregateMode }
            { name: 'PADDLE_OCR_URL', value: 'https://${paddleOcrApp.properties.configuration.ingress.fqdn}' }
            { name: 'PADDLE_CONFIDENCE_MEAN_MIN', value: paddleConfidenceMeanMin }
            { name: 'PADDLE_CONFIDENCE_LOW_FRAC_MAX', value: paddleConfidenceLowFracMax }
            { name: 'PADDLE_CONFIDENCE_WORD_MIN', value: paddleConfidenceWordMin }
            // Image quality preprocessing (OpenCV enhance_retry)
            { name: 'ENABLE_IMAGE_PREPROCESSING', value: toLower(string(enableImagePreprocessing)) }
            // Cost-effective summary model (empty = use main deployment)
            { name: 'SUMMARY_MODEL_DEPLOYMENT_NAME', value: summaryModelDeploymentName }
            // Cost controls and routing defaults
            { name: 'DEFAULT_EXTRACTION_TIER', value: defaultExtractionTier }
            { name: 'PRICING_USE_RETAIL_API', value: toLower(string(pricingUseRetailApi)) }
            { name: 'ROUTING_LOW_QUALITY_PAGE_FRACTION', value: routingLowQualityPageFraction }
            { name: 'ROUTING_LOW_QUALITY_MIN_PAGES', value: routingLowQualityMinPages }
            { name: 'ROUTING_MIN_OCR_TEXT_LENGTH', value: routingMinOcrTextLength }
            { name: 'ROUTING_ECONOMY_MAX_PAGES', value: routingEconomyMaxPages }
            // Mock flag-review email defaults
            { name: 'FLAG_EMAIL_FROM', value: 'noreply@argus.example' }
            { name: 'FLAG_EMAIL_TO_FALLBACK', value: 'uploader@argus.example' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: applicationInsightsConnectionString }
            { name: 'AZURE_CLIENT_ID', value: userManagedIdentityClientId }
            { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
            { name: 'AZURE_RESOURCE_GROUP_NAME', value: resourceGroup().name }
            { name: 'LOGIC_APP_NAME', value: 'logic-argus-v2-${resourceToken}' }
            { name: 'AZURE_STORAGE_ACCOUNT_NAME', value: storageAccountName }
            { name: 'AZURE_KEY_VAULT_URI', value: keyVaultUri }
            // OpenTelemetry + GenAI tracing
            { name: 'OTEL_SERVICE_NAME', value: containerAppName }
            { name: 'OTEL_TRACES_EXPORTER', value: 'otlp' }
            { name: 'OTEL_METRICS_EXPORTER', value: 'otlp' }
            { name: 'OTEL_LOGS_EXPORTER', value: 'otlp' }
            { name: 'OTEL_EXPORTER_OTLP_ENDPOINT', value: 'https://dc.applicationinsights.azure.com/v2/track' }
            { name: 'OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED', value: 'true' }
            { name: 'SEMANTICKERNEL_EXPERIMENTAL_GENAI_ENABLE_OTEL_DIAGNOSTICS', value: 'true' }
            { name: 'SEMANTICKERNEL_EXPERIMENTAL_GENAI_ENABLE_OTEL_DIAGNOSTICS_SENSITIVE', value: 'false' }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 5
        rules: [
          {
            name: 'http-rule'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
  tags: serviceResourceTags
}

resource paddleOcrApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-argus-paddleocr'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userManagedIdentityId}': {}
    }
  }
  properties: {
    environmentId: containerAppEnvironment.id
    configuration: {
      // Internal-only ingress: reachable from the backend within the Container Apps
      // environment, never exposed to the internet.
      ingress: {
        external: false
        targetPort: 8000
      }
      registries: [
        {
          server: containerRegistryLoginServer
          identity: userManagedIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'paddleocr'
          image: paddleOcrImage
          resources: {
            cpu: json('2.0')
            memory: '4Gi'
          }
          env: [
            { name: 'PADDLE_LANG', value: 'en' }
            { name: 'PADDLE_USE_ANGLE_CLS', value: 'true' }
            { name: 'PADDLE_WORD_MIN', value: paddleConfidenceWordMin }
          ]
        }
      ]
      // Scale-to-zero: the probe only runs during document processing, so idle cost
      // is ~free; cold start (model load) is acceptable for batch document flows.
      scale: {
        minReplicas: 0
        maxReplicas: 3
        rules: [
          {
            name: 'http-rule'
            http: {
              metadata: {
                concurrentRequests: '4'
              }
            }
          }
        ]
      }
    }
  }
  tags: union(tags, { 'azd-service-name': 'paddleocr' })
}

resource frontendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: frontendContainerAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userManagedIdentityId}': {}
    }
  }
  properties: {
    environmentId: containerAppEnvironment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 3000
      }
      secrets: [
        {
          name: 'api-key'
          keyVaultUrl: apiKeySecretUri
          identity: userManagedIdentityId
        }
      ]
      registries: [
        {
          server: containerRegistryLoginServer
          identity: userManagedIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: frontendContainerAppName
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            { name: 'BACKEND_API_KEY', secretRef: 'api-key' }
            { name: 'BLOB_ACCOUNT_URL', value: blobEndpoint }
            { name: 'CONTAINER_NAME', value: containerName }
            { name: 'COSMOS_URL', value: cosmosEndpoint }
            { name: 'COSMOS_DB_NAME', value: cosmosDatabaseName }
            { name: 'COSMOS_DOCUMENTS_CONTAINER_NAME', value: cosmosContainerName }
            { name: 'COSMOS_CONFIG_CONTAINER_NAME', value: cosmosConfigContainerName }
            { name: 'AZURE_CLIENT_ID', value: userManagedIdentityClientId }
            { name: 'BACKEND_URL', value: 'https://${containerApp.properties.configuration.ingress.fqdn}' }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 5
        rules: [
          {
            name: 'http-rule'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
  tags: union(tags, { 'azd-service-name': 'frontend' })
}

output containerAppName string = containerApp.name
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output paddleOcrAppName string = paddleOcrApp.name
output paddleOcrAppFqdn string = paddleOcrApp.properties.configuration.ingress.fqdn
output frontendAppName string = frontendApp.name
output frontendAppFqdn string = frontendApp.properties.configuration.ingress.fqdn
output containerAppEnvironmentId string = containerAppEnvironment.id
