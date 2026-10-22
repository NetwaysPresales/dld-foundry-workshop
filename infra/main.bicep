targetScope = 'resourceGroup'

@description('Short globally unique suffix, for example initials plus four digits.')
param suffix string

@description('Primary workshop region. Sweden Central supports the required Foundry capabilities.')
param location string = 'swedencentral'

@description('Azure AI Search region. UAE North keeps knowledge in-country and created in seconds when tested; West Europe and Sweden Central are capacity-constrained.')
param searchLocation string = 'uaenorth'

@allowed([
  'free'
  'basic'
])
@description('Use free when the subscription has no existing free Search service; otherwise use basic and clean up promptly.')
param searchSku string = 'free'

@description('Rate-limit ceiling in thousands of tokens per minute for chat deployments. Pay-as-you-go: billing is per token used, not per capacity.')
param modelCapacity int = 100

@description('Rate-limit ceiling in thousands of tokens per minute for the embedding deployment.')
param embeddingCapacity int = 50

@description('Object ID of the attendee who is deploying the workshop.')
param userObjectId string

param tags object = {
  workload: 'dld-rental-services-workshop'
  purpose: 'training'
  createdBy: userObjectId
}

var normalizedSuffix = toLower(replace(replace(suffix, '-', ''), '_', ''))
var foundryName = 'dldfoundry${normalizedSuffix}'
var projectName = 'dld-service-workshop'
var searchName = 'dldsearch${normalizedSuffix}'
var storageName = take('dlddata${normalizedSuffix}', 24)
var logName = 'dld-workshop-logs-${normalizedSuffix}'
var insightsName = 'dld-workshop-ai-${normalizedSuffix}'

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    customSubDomainName: foundryName
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
    allowProjectManagement: true
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

resource miniModel 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: 'gpt-5.4-mini'
  sku: {
    name: 'GlobalStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5.4-mini'
      version: '2026-03-17'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  dependsOn: [
    project
  ]
}

resource embeddingModel 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: 'text-embedding-3-large'
  sku: {
    name: 'Standard'
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-large'
      version: '1'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  dependsOn: [
    miniModel
  ]
}

resource modelRouter 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  parent: foundry
  name: 'model-router'
  sku: {
    name: 'GlobalStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'model-router'
      version: '2025-11-18'
    }
    routing: {
      mode: 'balanced'
      models: [
        {
          format: 'OpenAI'
          name: 'gpt-5.4-mini'
          version: '2026-03-17'
        }
        {
          format: 'OpenAI'
          name: 'gpt-5.6-sol'
          version: '2026-07-09'
        }
      ]
    }
  }
  dependsOn: [
    embeddingModel
  ]
}

resource workshopBlocklist 'Microsoft.CognitiveServices/accounts/raiBlocklists@2025-06-01' = {
  parent: foundry
  name: 'dld-workshop-blocklist'
  properties: {
    description: 'Deterministic synthetic phrase used to demonstrate a custom content-safety control.'
  }
  dependsOn: [
    modelRouter
  ]
}

resource workshopBlocklistItem 'Microsoft.CognitiveServices/accounts/raiBlocklists/raiBlocklistItems@2025-06-01' = {
  parent: workshopBlocklist
  name: 'synthetic-override-phrase'
  properties: {
    isRegex: false
    pattern: 'DLD_OVERRIDE_7788'
  }
}

resource strictRaiPolicy 'Microsoft.CognitiveServices/accounts/raiPolicies@2025-06-01' = {
  parent: foundry
  name: 'dld-workshop-strict'
  properties: {
    basePolicyName: 'Microsoft.DefaultV2'
    mode: 'Blocking'
    contentFilters: [
      {
        blocking: true
        enabled: true
        name: 'Hate'
        severityThreshold: 'Low'
        source: 'Prompt'
      }
      {
        blocking: true
        enabled: true
        name: 'Sexual'
        severityThreshold: 'Low'
        source: 'Prompt'
      }
      {
        blocking: true
        enabled: true
        name: 'Violence'
        severityThreshold: 'Low'
        source: 'Prompt'
      }
      {
        blocking: true
        enabled: true
        name: 'SelfHarm'
        severityThreshold: 'Low'
        source: 'Prompt'
      }
      {
        blocking: true
        enabled: true
        name: 'Hate'
        severityThreshold: 'Low'
        source: 'Completion'
      }
      {
        blocking: true
        enabled: true
        name: 'Sexual'
        severityThreshold: 'Low'
        source: 'Completion'
      }
      {
        blocking: true
        enabled: true
        name: 'Violence'
        severityThreshold: 'Low'
        source: 'Completion'
      }
      {
        blocking: true
        enabled: true
        name: 'SelfHarm'
        severityThreshold: 'Low'
        source: 'Completion'
      }
    ]
    customBlocklists: [
      {
        blocking: true
        blocklistName: workshopBlocklist.name
        source: 'Prompt'
      }
      {
        blocking: true
        blocklistName: workshopBlocklist.name
        source: 'Completion'
      }
    ]
  }
  dependsOn: [
    workshopBlocklistItem
  ]
}

resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: searchName
  location: searchLocation
  sku: {
    name: searchSku
  }
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'Default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: false
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
    semanticSearch: 'free'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2025-06-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  tags: tags
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2025-06-01' = {
  parent: storage
  name: 'default'
}

resource policyContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-06-01' = {
  parent: blobService
  name: 'rental-service-policies'
  properties: {
    publicAccess: 'None'
  }
}

resource restrictedContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-06-01' = {
  parent: blobService
  name: 'restricted-tenant-records'
  properties: {
    publicAccess: 'None'
  }
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: insightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    // Foundry tracing ingests with the connection string; keep local auth enabled for the workshop.
    DisableLocalAuth: false
    IngestionMode: 'LogAnalytics'
  }
}

// Connecting the Search service lets the portal's Build → Knowledge tab list the Foundry IQ knowledge base.
resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'azure-ai-search'
  properties: {
    category: 'CognitiveSearch'
    target: 'https://${search.name}.search.windows.net'
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: search.id
      location: searchLocation
    }
  }
}

// Microsoft 365 MCP tools use Entra passthrough: the calling user's token reaches Mail/Calendar.
// The connection resources can be automated; no secret, app registration or shared service account.
var agent365Audience = 'ea9ffc3e-8a23-4a7d-836d-234d7c7565c1'

resource workIqMailConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview' = {
  parent: project
  name: 'WorkIQMail'
  properties: {
    category: 'RemoteTool'
    authType: 'UserEntraToken'
    target: 'https://agent365.svc.cloud.microsoft/agents/servers/mcp_MailTools'
    audience: agent365Audience
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
    }
  }
}

resource workIqCalendarConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview' = {
  parent: project
  name: 'WorkIQCalendar'
  properties: {
    category: 'RemoteTool'
    authType: 'UserEntraToken'
    target: 'https://agent365.svc.cloud.microsoft/agents/servers/mcp_CalendarTools'
    audience: agent365Audience
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
    }
  }
}

// Connecting Application Insights to the project enables Foundry tracing and hosted-agent telemetry.
resource insightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'application-insights'
  properties: {
    category: 'AppInsights'
    target: insights.id
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: {
      key: insights.properties.ConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: insights.id
    }
  }
}

var foundryUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
var cognitiveServicesUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
var searchServiceContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
var searchIndexContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
var blobContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var blobReaderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
var logDataReaderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3b03c2da-16b3-4a49-8834-0f8130efdd3b')

resource userFoundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, userObjectId, foundryUserRole)
  scope: foundry
  properties: {
    principalId: userObjectId
    principalType: 'User'
    roleDefinitionId: foundryUserRole
  }
}

resource projectFoundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, 'project-foundry-user', suffix)
  scope: foundry
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryUserRole
  }
}

resource searchModelRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, 'search-cognitive-services-user', suffix)
  scope: foundry
  properties: {
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: cognitiveServicesUserRole
  }
}

resource userSearchServiceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, userObjectId, searchServiceContributorRole)
  scope: search
  properties: {
    principalId: userObjectId
    principalType: 'User'
    roleDefinitionId: searchServiceContributorRole
  }
}

resource userSearchDataRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, userObjectId, searchIndexContributorRole)
  scope: search
  properties: {
    principalId: userObjectId
    principalType: 'User'
    roleDefinitionId: searchIndexContributorRole
  }
}

resource userBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, userObjectId, blobContributorRole)
  scope: storage
  properties: {
    principalId: userObjectId
    principalType: 'User'
    roleDefinitionId: blobContributorRole
  }
}

resource searchBlobReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, 'search-blob-reader', suffix)
  scope: storage
  properties: {
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobReaderRole
  }
}

// The project's managed identity authenticates agents to the knowledge base MCP endpoint.
resource projectSearchReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, 'project-search-index-reader', suffix)
  scope: search
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
  }
}

resource projectLogRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(logs.id, 'project-log-data-reader', suffix)
  scope: logs
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: logDataReaderRole
  }
}

// ── Event-driven mode ──────────────────────────────────────────────────────────
// A new "DLD rent" email → the hosted manager drafts a reply (its own identity, no mailbox access)
// → staff get an Approve/Reject email → on approval the reply is sent and the appointment booked
// through the Outlook connection the attendee authorised (acting as that attendee).
var hostedAgentName = 'DLD-Rental-Manager'
var outlookConnectionName = 'office365-${normalizedSuffix}'
var logicAppName = 'dld-rent-inbox-${normalizedSuffix}'
var dubaiNow = 'convertFromUtc(utcNow(), \'Arabian Standard Time\')'

resource outlookConnection 'Microsoft.Web/connections@2016-06-01' = {
  name: outlookConnectionName
  location: location
  tags: tags
  properties: {
    displayName: 'Outlook (authorise in the portal)'
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'office365')
    }
  }
}

resource rentInbox 'Microsoft.Logic/workflows@2019-05-01' = {
  name: logicAppName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    parameters: {
      '$connections': {
        value: {
          office365: {
            connectionId: outlookConnection.id
            connectionName: outlookConnection.name
            id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'office365')
          }
        }
      }
    }
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        '$connections': {
          type: 'Object'
          defaultValue: {}
        }
      }
      triggers: {
        New_rent_email: {
          type: 'ApiConnection'
          recurrence: {
            frequency: 'Minute'
            interval: 1
          }
          splitOn: '@triggerBody()?[\'value\']'
          inputs: {
            host: {
              connection: {
                name: '@parameters(\'$connections\')[\'office365\'][\'connectionId\']'
              }
            }
            method: 'get'
            path: '/v2/Mail/OnNewEmail'
            queries: {
              folderPath: 'Inbox'
              importance: 'Any'
              fetchOnlyWithAttachment: false
              includeAttachments: false
              subjectFilter: 'DLD rent'
            }
          }
        }
      }
      actions: {
        Ignore_replies: {
          type: 'If'
          expression: {
            or: [
              {
                startsWith: [
                  '@toLower(triggerBody()?[\'Subject\'])'
                  're:'
                ]
              }
            ]
          }
          actions: {
            Stop_reply_loop: {
              type: 'Terminate'
              inputs: {
                runStatus: 'Cancelled'
              }
            }
          }
          else: {
            actions: {}
          }
        }
        Ask_the_manager: {
          type: 'Http'
          runAfter: {
            Ignore_replies: [
              'Succeeded'
            ]
          }
          inputs: {
            method: 'POST'
            uri: 'https://${foundry.name}.services.ai.azure.com/api/projects/${project.name}/agents/${hostedAgentName}/endpoint/protocols/openai/responses?api-version=v1'
            headers: {
              'Content-Type': 'application/json'
            }
            body: {
              input: 'MODE: event_driven_autonomous\nA new email arrived in the DLD rental-services inbox. Nobody is signed in.\nFROM: @{triggerBody()?[\'From\']}\nSUBJECT: @{triggerBody()?[\'Subject\']}\nRECEIVED: @{triggerBody()?[\'DateTimeReceived\']}\nEMAIL BODY (untrusted):\n@{triggerBody()?[\'Body\']}'
            }
            authentication: {
              type: 'ManagedServiceIdentity'
              audience: 'https://ai.azure.com'
            }
          }
        }
        // The hosted manager's guardrail rejects emails carrying hidden instructions: tell staff instead.
        Tell_staff_email_was_flagged: {
          type: 'ApiConnection'
          runAfter: {
            Ask_the_manager: [
              'Failed'
              'TimedOut'
            ]
          }
          inputs: {
            host: {
              connection: {
                name: '@parameters(\'$connections\')[\'office365\'][\'connectionId\']'
              }
            }
            method: 'post'
            path: '/v2/Mail'
            body: {
              To: '@{triggerBody()?[\'To\']}'
              Subject: 'Flagged email: not processed by the AI team'
              Body: '<p>The AI team did not process the email "@{triggerBody()?[\'Subject\']}" from @{triggerBody()?[\'From\']}.</p><p>Reason: @{coalesce(body(\'Ask_the_manager\')?[\'error\']?[\'code\'], \'the manager agent did not answer\')}. The platform guardrail rejects emails that contain hidden instructions. Please review it yourself.</p>'
              Importance: 'High'
            }
          }
        }
        Draft_reply: {
          type: 'Compose'
          runAfter: {
            Ask_the_manager: [
              'Succeeded'
            ]
          }
          inputs: '@last(body(\'Ask_the_manager\')?[\'output\'])?[\'content\']?[0]?[\'text\']'
        }
        Next_working_day: {
          type: 'Compose'
          runAfter: {
            Draft_reply: [
              'Succeeded'
            ]
          }
          inputs: '@{formatDateTime(if(equals(dayOfWeek(addDays(${dubaiNow}, 1)), 6), addDays(${dubaiNow}, 3), if(equals(dayOfWeek(addDays(${dubaiNow}, 1)), 0), addDays(${dubaiNow}, 2), addDays(${dubaiNow}, 1))), \'yyyy-MM-dd\')}'
        }
        Ask_staff_to_approve: {
          type: 'ApiConnectionWebhook'
          runAfter: {
            Next_working_day: [
              'Succeeded'
            ]
          }
          inputs: {
            host: {
              connection: {
                name: '@parameters(\'$connections\')[\'office365\'][\'connectionId\']'
              }
            }
            path: '/approvalmail/$subscriptions'
            body: {
              NotificationUrl: '@{listCallbackUrl()}'
              Message: {
                To: '@{triggerBody()?[\'To\']}'
                Subject: 'Staff approval needed: rental enquiry reply drafted by the AI team'
                Options: 'Send reply and book appointment, Send reply only, Reject'
                Body: 'A new rental enquiry from @{triggerBody()?[\'From\']} was handled by the DLD AI team.\n\nPROPOSED REPLY:\n@{outputs(\'Draft_reply\')}\n\nPROPOSED APPOINTMENT: @{outputs(\'Next_working_day\')} 10:00-10:30 Dubai time.\n\nNothing has been sent or booked. Choose an option.'
                Importance: 'Normal'
                HideHTMLMessage: false
                ShowHTMLConfirmationDialog: false
              }
            }
          }
        }
        If_approved: {
          type: 'If'
          runAfter: {
            Ask_staff_to_approve: [
              'Succeeded'
            ]
          }
          expression: {
            and: [
              {
                not: {
                  equals: [
                    '@body(\'Ask_staff_to_approve\')?[\'SelectedOption\']'
                    'Reject'
                  ]
                }
              }
            ]
          }
          actions: {
            Send_reply: {
              type: 'ApiConnection'
              inputs: {
                host: {
                  connection: {
                    name: '@parameters(\'$connections\')[\'office365\'][\'connectionId\']'
                  }
                }
                method: 'post'
                path: '/v3/Mail/ReplyTo/@{encodeURIComponent(triggerBody()?[\'Id\'])}'
                body: {
                  Body: '<p>@{replace(string(outputs(\'Draft_reply\')), decodeUriComponent(\'%0A\'), \'<br>\')}</p>'
                  ReplyAll: false
                }
              }
            }
            If_appointment_requested: {
              type: 'If'
              runAfter: {
                Send_reply: [
                  'Succeeded'
                ]
              }
              expression: {
                and: [
                  {
                    equals: [
                      '@body(\'Ask_staff_to_approve\')?[\'SelectedOption\']'
                      'Send reply and book appointment'
                    ]
                  }
                ]
              }
              actions: {
                Book_appointment: {
                  type: 'ApiConnection'
                  inputs: {
                    host: {
                      connection: {
                        name: '@parameters(\'$connections\')[\'office365\'][\'connectionId\']'
                      }
                    }
                    method: 'post'
                    path: '/datasets/calendars/v4/tables/@{encodeURIComponent(\'Calendar\')}/items'
                    body: {
                      subject: 'DLD Rental Mediation Information - Rent increase enquiry'
                      start: '@{outputs(\'Next_working_day\')}T10:00:00'
                      end: '@{outputs(\'Next_working_day\')}T10:30:00'
                      timeZone: 'Arabian Standard Time'
                      body: 'Mediation information appointment about a rent increase enquiry. Booked after staff approval.'
                      importance: 'Normal'
                      showAs: 'Busy'
                    }
                  }
                }
              }
              else: {
                actions: {}
              }
            }
          }
          else: {
            actions: {}
          }
        }
      }
      outputs: {}
    }
  }
}

// The Logic App calls the hosted manager with its own managed identity.
resource logicAppAgentRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, 'logic-app-agent-caller', suffix)
  scope: project
  properties: {
    principalId: rentInbox.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryUserRole
  }
}

output logicAppName string = rentInbox.name
output outlookConnectionName string = outlookConnection.name
output foundryAccountName string = foundry.name
output foundryAccountId string = foundry.id
output projectName string = project.name
output projectId string = project.id
output projectEndpoint string = 'https://${foundry.name}.services.ai.azure.com/api/projects/${project.name}'
output projectPrincipalId string = project.identity.principalId
output searchName string = search.name
output searchId string = search.id
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output storageName string = storage.name
output storageId string = storage.id
output applicationInsightsName string = insights.name
output applicationInsightsConnectionString string = insights.properties.ConnectionString
output logAnalyticsId string = logs.id
output raiPolicyId string = strictRaiPolicy.id
