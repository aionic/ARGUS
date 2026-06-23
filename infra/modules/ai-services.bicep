// Azure AI Foundry (AIServices account + project) with private endpoint and managed identity auth
param location string
param resourceToken string
param tags object
param azureOpenaiModelDeploymentName string
param privateEndpointsSubnetId string
param privateDnsZoneOpenAIId string
param privateDnsZoneCognitiveServicesId string
param privateDnsZoneAIServicesId string

@description('Resource name for the Azure AI Foundry project')
param foundryProjectName string = 'argus'

@description('Deploy a cheaper OpenAI model for the (text-only) summary stage cost experiment')
param deploySummaryModel bool = true

@description('Deployment name for the cheaper summary model')
param summaryModelDeploymentName string = 'gpt-4.1-mini'

@description('Cheaper summary model name (Azure OpenAI)')
param summaryModelName string = 'gpt-4.1-mini'

@description('Cheaper summary model version')
param summaryModelVersion string = '2025-04-14'

@description('Capacity (TPM in thousands) for the cheaper summary model')
param summaryModelCapacity int = 50

@description('SKU for the cheaper summary model. gpt-4.1-mini is only offered as Standard (not GlobalStandard).')
param summaryModelSku string = 'Standard'

@description('Experiment: deploy a Phi serverless model for cost comparison. Verify Phi availability in the region before enabling.')
param deployPhiModel bool = false

@description('Deployment name for the Phi model experiment')
param phiModelDeploymentName string = 'phi-4'

@description('Phi model name')
param phiModelName string = 'Phi-4'

@description('Phi model version')
param phiModelVersion string = '7'

// ─── AI Foundry account (kind: AIServices with project management) ───
resource aiServices 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: 'aoai-${resourceToken}'
  location: location
  sku: {
    name: 'S0'
  }
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: 'aoai-${resourceToken}'
    allowProjectManagement: true
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
    networkAcls: {
      defaultAction: 'Deny'
    }
  }
  tags: tags
}

// ─── Foundry project (child of the AIServices account) ───
resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: aiServices
  name: foundryProjectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'ARGUS'
    description: 'ARGUS document extraction agents'
  }
  tags: tags
}

// ─── Model deployment (on the account) ───
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: aiServices
  name: azureOpenaiModelDeploymentName
  // Serialize after the project: both are account children that mutate the
  // account, and creating them in parallel causes an ETag/If-Match race
  // (IfMatchPreconditionFailed) on the account.
  dependsOn: [
    foundryProject
  ]
  sku: {
    name: 'GlobalStandard'
    capacity: 800
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5.4'
      version: '2026-03-05'
    }
  }
}

// ─── Cheaper summary model deployment (cost experiment, text-only stage) ───
resource summaryModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = if (deploySummaryModel) {
  parent: aiServices
  name: summaryModelDeploymentName
  // Serialize after the primary deployment to avoid the account ETag/If-Match race.
  dependsOn: [
    modelDeployment
  ]
  sku: {
    name: summaryModelSku
    capacity: summaryModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: summaryModelName
      version: summaryModelVersion
    }
  }
}

// ─── Phi serverless model deployment (experiment; disabled by default) ───
// Phi models deploy with format 'Microsoft'. Verify availability in the target
// region before enabling via deployPhiModel=true.
resource phiModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = if (deployPhiModel) {
  parent: aiServices
  name: phiModelDeploymentName
  dependsOn: [
    modelDeployment
    summaryModelDeployment
  ]
  sku: {
    name: 'GlobalStandard'
    capacity: 1
  }
  properties: {
    model: {
      format: 'Microsoft'
      name: phiModelName
      version: phiModelVersion
    }
  }
}

resource openaiPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-openai-${resourceToken}'
  location: location
  properties: {
    subnet: {
      id: privateEndpointsSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'openai-connection'
        properties: {
          privateLinkServiceId: aiServices.id
          groupIds: ['account']
        }
      }
    ]
  }
  tags: tags
  dependsOn: [
    modelDeployment
    summaryModelDeployment
    phiModelDeployment
    foundryProject
  ]
}

// AI Foundry private endpoints must resolve across all three zones
resource openaiDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: openaiPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'openai'
        properties: {
          privateDnsZoneId: privateDnsZoneOpenAIId
        }
      }
      {
        name: 'cognitiveservices'
        properties: {
          privateDnsZoneId: privateDnsZoneCognitiveServicesId
        }
      }
      {
        name: 'aiservices'
        properties: {
          privateDnsZoneId: privateDnsZoneAIServicesId
        }
      }
    ]
  }
}

output aiServicesId string = aiServices.id
output aiServicesEndpoint string = aiServices.properties.endpoint
output aiServicesName string = aiServices.name
output foundryProjectName string = foundryProject.name
output foundryProjectEndpoint string = 'https://${aiServices.name}.services.ai.azure.com/api/projects/${foundryProject.name}'
output summaryModelDeploymentName string = deploySummaryModel ? summaryModelDeploymentName : ''
