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
