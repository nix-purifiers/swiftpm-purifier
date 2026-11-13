Raw native Swift 5.3.3 / SwiftPM 5.3.0 records from the original Linux SDK.
Workspace schema 4 uses path references; the native553 fixture uses location
references at the same schema number. The graph has real semantic-version Git
pins and declared package names distinct from URL basenames. These frozen
loopback URLs are codec data; unit tests never fetch them.
